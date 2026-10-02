"""IMP-C25 Step 9 — `map_photos`: map the uploaded library objects to the Google Photos inventory (PLAN Step 9).

Pinned here, per sub-task:
  9.1  the freshness gate (no inventory / no complete enumeration / older than 24 h / older than the newest capture
       push -> stale-inventory + the re-crawl command); the CLI; the scope (all / id / prefix / alias, --account by
       routing); a dry run writes nothing but its run directory (library files hashed before and after);
       compatibility with the REAL crawler's files (a crawl of the fake Google Photos).
  9.2  the name ladder: exact -> P-6 normalized -> another account (a 31-episode wrong-account block + whether the
       mvconfig overrides cover it; copies; evidence from a stale inventory refused) -> `[short_id]`-tag probable;
       possibly-deleted items never match.
  9.3  the content key (capture SHA-1 = dedupKey): proven / conflict / another name / another account.
  9.4  duplicates: the same bytes twice; re-split uploads (newest complete set, newer-incomplete safety, a pinned
       set, an extras split).
  9.5  IMP-C27: split_info lost (+ the proven repair and its writer), old untagged names (+ the apply engine's
       `names=`), partial / non-original copies, every device verdict (freed vs on a phone vs absent vs unchecked)
       with partial sets and the LOUD block, the extra movies phone (DECISIONS §1d).
  9.6  the unmatched-inventory report.   9.7  resolutions_todo.csv / inconsistencies.csv / the failed records;
       mapping.csv (the R1 reader's sheet: wrong-account list, near-matches, duplicates); an unwritable report file.
  9.8  --resolutions and the hints store.   9.9  --apply (sidecar, engine, failed list, CSV rule, idempotence).
  9.10 --devices-only.   Plus: all four prefixes end to end, the CLI dispatcher branch.
Disarm probes (on an isolated scratch copy): PROGRESS.md row 9.
SYNTHETIC data only: photo ids contain FAKE, devices are FAKE serials; the library lives in the `sandbox`, the
inventory and the Google Photos state in the conftest redirects (tmp_path) — never D:\\ or ~/.mediavault.
"""
import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

import main
import mvcommon

REAL_DEVICE_INVENTORY = main.device_inventory            # (World replaces it with a synthetic check)
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
ENUMERATED = NOW - timedelta(hours=1)        # a fresh complete enumeration


def _iso(when):
    return when.isoformat(timespec="seconds")                  # the crawler's now_iso() form: '...+00:00'


def _ms(text):
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()) * 1000


def _key(data):
    """The dedupKey of some bytes — computed here, independently of the code under test (RESEARCH F21)."""
    return base64.urlsafe_b64encode(hashlib.sha1(data).digest()).decode("ascii").rstrip("=")


def _h(tag):
    return hashlib.sha256(tag.encode()).hexdigest()


# ============================================================================ the inventory (the crawler's files)

class Inventory:
    """Writes <account>.items.jsonl + <account>.runs.jsonl exactly in tools/gp_inventory.py's documented format into
    mvcommon.GP_INVENTORY_DIR (conftest points it under tmp_path). `enumerated[account]` = the start of that account's
    complete enumeration (None: never enumerated — a legacy crawl without a run log); an account with no item and no
    enumeration has no files at all (never crawled)."""

    def __init__(self):
        self.items = {a: [] for a in mvcommon.GP_ACCOUNTS}
        self.enumerated = {a: ENUMERATED for a in ("movies", "tv", "anime")}
        self.unlisted = {a: set() for a in mvcommon.GP_ACCOUNTS}      # ids the enumeration did not see (deleted)
        self.undetailed = {a: [] for a in mvcommon.GP_ACCOUNTS}       # ids listed but never detailed
        self._n = 0

    def add(self, account, filename, data=None, taken="2024-03-09T06:03:02Z", upload_ms=None, **facts):
        """One detailed item -> its record (photo_id, dedup_key ... as the crawler writes them)."""
        self._n += 1
        pid = f"AF1QipFAKEmap{self._n:05d}{account[:3]}0123456789"
        data = data if data is not None else f"FAKE-BYTES:{account}:{self._n}:{filename}".encode()
        taken_ms = _ms(taken)
        rec = {"photo_id": pid, "filename": filename, "width": 1920, "height": 1080, "taken_ms": taken_ms,
               "dedup_key": _key(data), "tz_offset_ms": 28_800_000,
               "upload_ms": upload_ms if upload_ms is not None else taken_ms + 86_400_000}
        rec.update(facts)
        rec.update(account=account, crawled_at=_iso(ENUMERATED - timedelta(minutes=30)), source="rpc")
        self.items[account].append(rec)
        return rec

    def write(self):
        folder = mvcommon.GP_INVENTORY_DIR
        os.makedirs(folder, exist_ok=True)
        for account in mvcommon.GP_ACCOUNTS:
            items, start = self.items[account], self.enumerated.get(account)
            if not items and start is None and not self.undetailed[account]:
                for kind in ("items", "runs"):
                    path = os.path.join(folder, f"{account}.{kind}.jsonl")
                    if os.path.exists(path):
                        os.remove(path)
                continue
            with open(os.path.join(folder, f"{account}.items.jsonl"), "w", encoding="utf-8") as fh:
                for rec in items:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            runs = os.path.join(folder, f"{account}.runs.jsonl")
            if start is None:
                if os.path.exists(runs):
                    os.remove(runs)
                continue
            seen = [r["photo_id"] for r in items if r["photo_id"] not in self.unlisted[account]]
            seen += self.undetailed[account]
            lines = [{"run": "run001", "event": "start", "at": _iso(start), "account": account, "mode": "rpc",
                      "phase": "all"}]
            lines += [{"photo_id": pid, "seen_run": "run001"} for pid in seen]
            lines += [{"run": "run001", "event": "enumerated", "at": _iso(start + timedelta(minutes=2)),
                       "mode": "rpc", "ids": len(seen)},
                      {"run": "run001", "event": "end", "at": _iso(start + timedelta(minutes=5)), "result": "ok",
                       "detailed": len(items), "upgraded": 0, "errors": 0, "mode": "rpc", "new": len(seen)}]
            with open(runs, "w", encoding="utf-8") as fh:
                for line in lines:
                    fh.write(json.dumps(line) + "\n")


# ============================================================================ the synthetic library (all four prefixes)

M_WHOLE = "mov-en-2019-mpwhole"            # unsplit movie
M_SPLIT = "mov-en-2020-mpsplit"            # split: 3 chunks + a FLAC holder
T_SEASON = "tv-en-2015-mpshow-s02"         # season_map (extras: Recap)
T_EP1 = "tv-en-2015-mpshow-s02e01"
T_EP2 = "tv-en-2015-mpshow-s02e02"         # combined S02E02E03 file
T_ALIAS = "tv-en-2015-mpshow-s02e03"       # multi_ep_alias -> T_EP2
A_EP = "ani-ja-2012-mpanime01"
O_MATCH = "oth-en-2024-mpfinal"


def leaf(mid, folder, filename, chunks=0, holder=False, status="archived"):
    sid = mvcommon.generate_short_id(mid)
    stem, ext = os.path.splitext(filename)
    entry = {"short_id": sid, "filename": filename, "folder_path": str(folder), "status": status, "uploaded": True,
             "search_term": f"{stem} [{sid}]{ext}", "hash": _h(mid)}
    if chunks:
        entry["split_info"] = {"is_split": True, "method": "SIZE_GB", "val": "4", "total_chunks": chunks,
                               "chunks": [{"filename": f"{stem} [{sid}].chunk.{i:03d}.mkv", "hash": _h(f"{mid}#{i}")}
                                          for i in range(1, chunks + 1)]}
        if holder:
            entry["split_info"]["carried_out_tracks"] = [{
                "track_id": 2, "codec": "A_FLAC", "language": "jpn", "holder_filename": f"{stem} [{sid}].holder.mkv",
                "holder_hash": _h(f"{mid}#holder")}]
    return entry


def extra(title_id, sub_rel, chunks=0):
    sid = mvcommon.generate_short_id(f"{title_id}::Specials/{sub_rel}")
    stem, ext = os.path.splitext(sub_rel)
    item = {"filename": sub_rel, "sub_rel": sub_rel, "short_id": sid, "hash": _h(f"{title_id}::{sub_rel}"),
            "status": "archived", "uploaded": True, "search_term": f"{stem} [{sid}]{ext}"}
    if chunks:
        item["split_info"] = {"is_split": True, "method": "COUNT", "val": str(chunks), "total_chunks": chunks,
                              "chunks": [{"filename": f"{stem} [{sid}].chunk.{i:03d}.mkv", "hash": _h(f"{sid}#{i}")}
                                         for i in range(1, chunks + 1)]}
    return item


class World:
    """The sandbox library + its inventory + a fake device check + the pinned clock. `names[(id, label)]` is each
    object's uploaded name as mvcommon.iter_uploaded_objects gives it."""

    def __init__(self, sandbox, monkeypatch):
        self.sandbox, self.monkeypatch = sandbox, monkeypatch
        self.root = sandbox["local_root"]
        self.inv = Inventory()
        self.library = {}
        self.devices = {"complete": True, "unconfigured": [], "devices": {"movies": self._phone("movies", "FAKEMOV0001"),
                                                                          "series": self._phone("series", "FAKESER0002")}}
        self.device_calls = 0
        monkeypatch.setattr(mvcommon, "_now_utc", lambda: NOW)
        monkeypatch.setattr(main, "device_inventory", self._device_inventory)

    @staticmethod
    def _phone(alias, serial, files=()):
        return {"alias": alias, "serial": serial, "status": "ok", "detail": None, "root": "/sdcard/Media/",
                "files": [{"path": "/sdcard/Media/X/" + name, "name": name, "size": size, "mtime": 1}
                          for name, size in files], "stat_errors": 0}

    def _device_inventory(self, aliases=None):
        self.device_calls += 1
        return json.loads(json.dumps(self.devices))

    def on_phone(self, alias, name, size=None):
        self.devices["devices"][alias]["files"].append(
            {"path": "/sdcard/Media/X/" + name, "name": name, "size": size, "mtime": 1})

    def folder(self, *parts):
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        assert "C:\\Media" not in str(path.resolve())
        return path

    def base(self):
        """The default library: every object kind over the four prefixes."""
        show = self.folder("Series", "MP Show", "Season 02")
        self.library.update({
            M_WHOLE: leaf(M_WHOLE, self.folder("Movies", "MP Whole (2019)"), "MP.Whole.2019.1080p.mkv"),
            M_SPLIT: {**leaf(M_SPLIT, self.folder("Movies", "MP Split (2020)"), "MP.Split.2020.2160p.mkv",
                             chunks=3, holder=True),
                      "extras": {"groups": {"Specials": {"added_date": "2026-09-01",
                                                         "items": [extra(M_SPLIT, "BTS.mkv"),
                                                                   extra(M_SPLIT, "Making Of.mkv", chunks=2)]}}}},
            T_SEASON: {"type": "season_map", "folder_path": str(show), "total_episodes": 3,
                       "children": [T_EP1, T_EP2, T_ALIAS],
                       "extras": {"groups": {"Specials": {"added_date": "2026-09-01",
                                                          "items": [extra(T_SEASON, "Recap.mkv")]}}}},
            T_EP1: {**leaf(T_EP1, show, "MP.Show.S02E01.mkv"), "parent_id": T_SEASON},
            T_EP2: {**leaf(T_EP2, show, "MP.Show.S02E02E03.mkv"), "parent_id": T_SEASON},
            T_ALIAS: {"type": "multi_ep_alias", "alias_of": T_EP2, "parent_id": T_SEASON},
            A_EP: leaf(A_EP, self.folder("Anime", "MP Anime"), "MP Anime - 01 [1080p].mkv"),
            O_MATCH: leaf(O_MATCH, self.folder("Sports", "MP Final"), "MP.Final.2024.mkv"),
        })
        return self

    def save(self):
        mvcommon.save_library(self.library)
        self.inv.write()
        self.names = {(o["manual_id"], o["label"]): o["uploaded_name"]
                      for o in mvcommon.iter_uploaded_objects(mvcommon.load_library())}
        return self

    def upload_all(self, skip=(), accounts=("movies", "tv", "anime")):
        """Put every uploaded object of the library into its default account's inventory — for the `accounts` that
        were crawled (by default not `others`: like the real library, that account has no profile yet) — except
        `skip` (ids or (id, label) keys)."""
        library = mvcommon.load_library() if not self.library else self.library
        for o in mvcommon.iter_uploaded_objects(library):
            if (o["manual_id"], o["label"]) in skip or o["manual_id"] in skip:
                continue
            account = mvcommon.category_for_id(o["manual_id"])
            if account in accounts:
                self.inv.add(account, o["uploaded_name"])
        return self

    def capture(self, mid, sha256, sha1, pushed_at, uploaded_name, role="whole", index=None, folder=None):
        entry = self.library[mid]
        folder = folder or entry["folder_path"]
        path = os.path.join(folder, f"{entry['short_id']}.gpcapture.json")
        doc = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {
            "capture_version": 2, "manual_id": mid, "short_id": entry["short_id"], "pushes": []}
        doc["pushes"].append({"captured_at": pushed_at, "device_id": "FAKEMOV0001", "device_tz": "Asia/Singapore",
                              "device_tz_offset_min": 480, "remote_dir": "/sdcard/Media/X", "chunk_range": None,
                              "complete": True, "objects": [{
                                  "role": role, "index": index, "sha256": sha256, "sha1": sha1,
                                  "dedup_key": base64.urlsafe_b64encode(bytes.fromhex(sha1)).decode().rstrip("="),
                                  "uploaded_name": uploaded_name, "size_bytes": 1000,
                                  "date_utc": "2024-03-09T06:03:02Z"}]})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def run(self, *tokens, out=None, capsys=None):
        opts = main.parse_map_photos_args(list(tokens))
        if out is not None:
            opts["out"] = str(out)
        result = main.cmd_map_photos(**opts)
        if capsys is not None:
            result["stdout"] = capsys.readouterr().out
        return result


@pytest.fixture()
def world(sandbox, monkeypatch):
    return World(sandbox, monkeypatch).base()


def by_key(result):
    return {(r["manual_id"], r["label"]): r for r in result["rows"]}


def library_files(sandbox):
    return {name: (sandbox[name].read_bytes(), os.stat(sandbox[name]).st_mtime_ns)
            for name in ("lib_movies", "lib_series", "lib_anime", "lib_others")}


def state_files():
    """{relative path: bytes} of everything in the Google Photos state dir."""
    root = mvcommon.GPHOTOS_STATE_DIR
    out = {}
    for folder, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            out[os.path.relpath(path, root)] = open(path, "rb").read()
    return out


# ============================================================================ 9.1 the CLI

@pytest.mark.parametrize("tokens,expected", [
    ([], {}),
    (["all"], {}),
    (["tv-en-2015-mpshow-s02"], {"scope": "tv-en-2015-mpshow-s02"}),
    (["--account", "tv", "--apply"], {"account": "tv", "apply": True}),
    (["mov-", "--resolutions", r"C:\x\failed_objects.csv", "--repair", "split-info", "--repair", "names"],
     {"scope": "mov-", "resolutions": r"C:\x\failed_objects.csv", "repairs": ("split-info", "names")}),
    (["--repair", "names,split-info,names", "--accept-probable", "--out", r"D:\out"],
     {"repairs": ("names", "split-info"), "accept_probable": True, "out": r"D:\out"}),
    (["all", "--devices-only", "--account", "movies"], {"devices_only": True, "account": "movies"}),
])
def test_the_cli_parses_every_documented_option(tokens, expected):
    defaults = {"scope": "all", "account": None, "devices_only": False, "resolutions": None, "repairs": (),
                "accept_probable": False, "apply": False, "out": None}
    assert main.parse_map_photos_args(tokens) == {**defaults, **expected}


@pytest.mark.parametrize("tokens,fragment", [
    (["--account", "films"], "--account must be one of"),
    (["--account"], "--account needs a value"),
    (["--repair", "everything"], "--repair takes"),
    (["--out", "--apply"], "--out needs a value"),
    (["--force"], "unknown option --force"),
    (["mov-a", "mov-b"], "one scope only"),
    (["--devices-only", "--apply"], "--devices-only takes only"),
    (["--devices-only", "--repair", "names"], "--devices-only takes only"),
])
def test_the_cli_refuses_what_it_does_not_know(tokens, fragment):
    with pytest.raises(ValueError, match=fragment.replace("-", r"\-")):
        main.parse_map_photos_args(tokens)


def test_the_usage_line_documents_the_plan_cli():
    assert main.MAP_USAGE == ("map_photos [all|<id>|<prefix>] [--account movies|tv|anime|others] [--devices-only] "
                              "[--resolutions <csv>] [--repair split-info|names] [--accept-probable] [--apply] "
                              "[--out <dir>]")


# ============================================================================ 9.1 the freshness gate

def test_a_fresh_inventory_gives_verdicts(world, tmp_path, capsys):
    world.save().upload_all().save()
    res = world.run(out=tmp_path / "run", capsys=capsys)
    rows = by_key(res)
    assert rows[(M_WHOLE, "whole")]["status"] == "mapped"
    assert rows[(T_EP1, "whole")]["status"] == "mapped" and rows[(A_EP, "whole")]["status"] == "mapped"
    assert rows[(O_MATCH, "whole")]["status"] == "stale-inventory"        # the others account was never crawled
    fresh = res["fresh"]
    assert [fresh[a]["fresh"] for a in mvcommon.GP_ACCOUNTS] == [True, True, True, False]
    assert fresh["tv"]["as_of"] == "2026-10-01T11:00:00Z" and fresh["tv"]["age_h"] == pytest.approx(1.0)
    assert fresh["others"]["reason"] == "not crawled yet"
    out = res["stdout"]
    assert "  tv      fresh — complete enumeration as of 2026-10-01T11:00:00Z (1.0 h ago)" in out
    assert "  others  STALE — not crawled yet — re-crawl: python tools/gp_inventory.py others --port <port>" in out


@pytest.mark.parametrize("case,reason", [
    ("legacy", "no complete enumeration yet"),                      # the files crawled before the run log existed
    ("old", "the enumeration is 25.0 h old (limit 24 h)"),
    ("push", "a push at 2026-10-01T11:30:00Z is newer than the enumeration (2026-10-01T11:00:00Z)"),
])
def test_a_stale_account_gives_no_verdict_and_names_the_recrawl(world, tmp_path, capsys, case, reason):
    world.save().upload_all()
    if case == "legacy":
        world.inv.enumerated["tv"] = None
    elif case == "old":
        world.inv.enumerated["tv"] = NOW - timedelta(hours=25)
    world.save()
    if case == "push":   # a capture push of an in-scope tv object after the enumeration: it may not be backed up yet
        world.capture(T_EP1, _h(T_EP1), hashlib.sha1(b"ep1").hexdigest(), "2026-10-01T11:30:00Z",
                      world.names[(T_EP1, "whole")])
    res = world.run(out=tmp_path / "run", capsys=capsys)
    rows = by_key(res)
    for key, row in rows.items():
        if row["default_account"] == "tv":
            assert (row["status"], row["note"]) == ("stale-inventory", f"tv: {reason}"), key
            assert row["item"] is None and row["proof"] is None
    assert rows[(M_WHOLE, "whole")]["status"] == "mapped"                 # other accounts are unaffected
    assert f"  tv      STALE — {reason} — re-crawl: python tools/gp_inventory.py tv --port <port>" in res["stdout"]


def test_a_push_counts_for_the_account_it_is_routed_to(world, tmp_path, monkeypatch):
    """The capture push of a tv id that an mvconfig override routes to MOVIES makes the movies inventory stale, not
    the tv one (the object will be backed up to the account of the phone it was pushed from)."""
    world.save().upload_all().save()
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"fetch_account_overrides": {T_EP1: "movies"}})
    world.capture(T_EP1, _h(T_EP1), hashlib.sha1(b"ep1").hexdigest(), "2026-10-01T11:30:00Z",
                  world.names[(T_EP1, "whole")])
    fresh = world.run(out=tmp_path / "run")["fresh"]
    assert (fresh["movies"]["fresh"], fresh["tv"]["fresh"]) == (False, True)
    assert fresh["movies"]["newest_push"] == "2026-10-01T11:30:00Z"


def test_an_out_of_scope_push_does_not_block_a_scoped_run(world, tmp_path):
    world.save().upload_all().save()
    world.capture(T_EP1, _h(T_EP1), hashlib.sha1(b"ep1").hexdigest(), "2026-10-01T11:30:00Z",
                  world.names[(T_EP1, "whole")])
    res = world.run(T_EP2, out=tmp_path / "run")
    assert [(r["manual_id"], r["status"]) for r in res["rows"]] == [(T_EP2, "mapped")]
    assert res["fresh"]["tv"]["fresh"] is True


# ============================================================================ 9.1 scope, read-only, the real crawler

def test_the_scope_selects_ids_prefixes_and_resolves_aliases(world, tmp_path, capsys):
    world.save().upload_all().save()
    season = world.run(T_SEASON, out=tmp_path / "a")               # a prefix: the season_map + its episodes
    assert [(r["manual_id"], r["label"]) for r in season["rows"]] == [
        (T_SEASON, "extra Specials/Recap.mkv"), (T_EP1, "whole"), (T_EP2, "whole")]
    alias = world.run(T_ALIAS, out=tmp_path / "b")                 # an alias id maps its primary, once
    assert [(r["manual_id"], r["label"]) for r in alias["rows"]] == [(T_EP2, "whole")]
    capsys.readouterr()
    missing = world.run("tv-xx-none", out=tmp_path / "c", capsys=capsys)
    assert missing["status"] == "error" and "no library id is or starts with 'tv-xx-none'" in missing["stdout"]
    assert not (tmp_path / "c").exists()


def test_account_selects_objects_by_their_routing_account(world, tmp_path, monkeypatch):
    world.save().upload_all().save()
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"fetch_account_overrides": {T_EP1: "movies"}})
    rows = world.run("--account", "movies", out=tmp_path / "run")["rows"]
    assert {r["manual_id"] for r in rows} == {M_WHOLE, M_SPLIT, T_EP1}
    ep = next(r for r in rows if r["manual_id"] == T_EP1)
    assert (ep["default_account"], ep["override"], ep["routing"]) == ("tv", "movies", "movies")


def test_a_dry_run_writes_nothing_but_its_run_directory(world, capsys):
    """PLAN Step 9 rules: the library is read-only until --apply; a dry run writes nothing to the library files, the
    learned sidecars, the hints or the failed list. Library files hashed (bytes + mtime) before and after."""
    world.save().upload_all().save()
    before, state_before = library_files(world.sandbox), state_files()
    res = world.run(capsys=capsys)
    assert library_files(world.sandbox) == before
    written = set(state_files()) - set(state_before)
    run = os.path.relpath(res["run_dir"], mvcommon.GPHOTOS_STATE_DIR)
    assert run.startswith(os.path.join("map", "20261001T120000Z")) and written
    assert all(path.startswith(run + os.sep) for path in written), written       # only the run directory
    assert not [p for p in state_files() if p.startswith(("learned_", "failed", "hints"))]
    assert {"mapping.jsonl", "summary.txt"} <= set(os.listdir(res["run_dir"]))
    rows = [json.loads(line) for line in open(os.path.join(res["run_dir"], "mapping.jsonl"), encoding="utf-8")]
    assert rows == json.loads(json.dumps(res["rows"]))
    assert open(os.path.join(res["run_dir"], "summary.txt"), encoding="utf-8").read() == res["stdout"]


def test_the_report_names_no_photo_id_or_dedup_key(world, tmp_path, capsys):
    world.save().upload_all().save()
    out = world.run(out=tmp_path / "run", capsys=capsys)["stdout"]
    for account in mvcommon.GP_ACCOUNTS:
        for rec in world.inv.items[account]:
            assert rec["photo_id"] not in out and rec["dedup_key"] not in out
    assert "AF1Qip" not in out


def test_the_real_crawler_files_are_read_through_its_own_reader(world, fake_photos, tmp_path, monkeypatch):
    """Drift pin: an inventory written by the REAL crawler (tools/gp_inventory.crawl over the fake Google Photos,
    rpc mode) maps — the mapper reads it only through the crawler's inventory_state, never a private parser."""
    from tools import gp_inventory
    import gprpc
    monkeypatch.setattr(gp_inventory, "now_iso", lambda: _iso(ENUMERATED))
    monkeypatch.setattr(gp_inventory, "time", type("T", (), {"time": staticmethod(lambda: 0.0),
                                                              "sleep": staticmethod(lambda s: None)}))
    monkeypatch.setattr(gprpc, "_sleep", lambda s: None)
    world.save()
    world.inv.enumerated = {}
    world.inv.write()                                           # no hand-written files at all
    for o in mvcommon.iter_uploaded_objects(mvcommon.load_library()):
        if o["category"] in ("movies", "tv", "anime"):
            fake_photos.add_item(o["category"], o["uploaded_name"])
    for account in ("movies", "tv", "anime"):
        result = gp_inventory.crawl(fake_photos.driver(account), account, mvcommon.GP_INVENTORY_DIR, log=lambda m: None)
        assert result["result"] == "ok"
    rows = by_key(world.run(out=tmp_path / "run"))
    assert {k: r["status"] for k, r in rows.items() if r["category"] != "others"} == {
        k: "mapped" for k, r in rows.items() if r["category"] != "others"}
    item = rows[(M_WHOLE, "whole")]["item"]
    fake = next(i for i in fake_photos.items("movies") if i.filename == world.names[(M_WHOLE, "whole")])
    assert (item["photo_id"], item["dedup_key"], item["taken_ms"]) == (fake.photo_id, fake.dedup_key, fake.taken_ms)


# ============================================================================ 9.2 the name ladder

def test_every_object_kind_of_the_crawled_categories_maps_by_its_exact_name(world, tmp_path):
    world.save().upload_all().save()
    rows = by_key(world.run(out=tmp_path / "run"))
    expected_kinds = {"whole", "chunk", "holder", "extra_whole", "extra_chunk"}
    mapped = {k: r for k, r in rows.items() if r["category"] != "others"}
    assert {r["kind"] for r in mapped.values()} == expected_kinds
    for key, row in mapped.items():
        assert (row["status"], row["name_match"], row["proof"], row["account"]) == (
            "mapped", "exact", "filename", row["category"]), key
        assert row["item"]["filename"] == world.names[key] and row["item"]["account"] == row["category"]


def test_a_whitespace_near_match_maps_normalized_and_exact_wins(world, tmp_path):
    """PLAN P-6: the crawler's innerText collapses whitespace runs (the 19 real Tamil movie names)."""
    mid = "mov-ta-2018-mpspaced"
    world.library[mid] = leaf(mid, world.folder("Movies", "MP Spaced"), "MP  Spaced  Title.2018.mkv")
    world.save().upload_all(skip={mid})
    name = world.names[(mid, "whole")]
    collapsed = " ".join(name.split())
    assert collapsed != name
    world.inv.add("movies", collapsed.upper().replace("MKV", "mkv"))           # case + whitespace: normalized only
    world.save()
    row = by_key(world.run(mid, out=tmp_path / "a"))[(mid, "whole")]
    assert (row["status"], row["name_match"], row["proof"]) == ("mapped-normalized", "normalized", "filename")
    exact = world.inv.add("movies", name)
    world.save()
    row = by_key(world.run(mid, out=tmp_path / "b"))[(mid, "whole")]
    assert (row["status"], row["name_match"], row["item"]["photo_id"]) == ("mapped", "exact", exact["photo_id"])


def test_a_normalized_name_in_the_default_account_beats_an_exact_name_elsewhere(world, tmp_path):
    world.save().upload_all(skip={M_WHOLE})
    name = world.names[(M_WHOLE, "whole")]
    world.inv.add("movies", name.upper())
    world.inv.add("tv", name)
    world.save()
    row = by_key(world.run(M_WHOLE, out=tmp_path / "run"))[(M_WHOLE, "whole")]
    assert (row["status"], row["account"], row["name_match"]) == ("mapped-normalized", "movies", "normalized")
    assert row["copies"] == [] and "a same-named item with other bytes is also in tv" in row["note"]


XFILES = [f"tv-en-1995-mpxfiles-s02e{n:02d}" for n in range(1, 32)]


@pytest.fixture()
def xfiles(world, monkeypatch):
    """The IMP-C26 shape: a 31-episode tv block whose items live ONLY in the MOVIES account. Overrides: three prefix
    keys cover e01-e29, e30 has none, e31's exact key routes to anime (it beats the prefix rule)."""
    season = world.folder("Series", "MP X", "Season 02")
    for mid in XFILES:
        world.library[mid] = leaf(mid, season, f"MP.X.S02E{mid[-2:]}.mkv")
    world.save().upload_all(skip=set(XFILES))
    for mid in XFILES:
        world.inv.add("movies", world.names[(mid, "whole")])
    world.save()
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"fetch_account_overrides": {
        "tv-en-1995-mpxfiles-s02e0": "movies", "tv-en-1995-mpxfiles-s02e1": "movies",
        "tv-en-1995-mpxfiles-s02e2": "movies", "tv-en-1995-mpxfiles-s02e31": "anime"}})
    return world


def test_a_wrong_account_block_and_whether_overrides_cover_it(xfiles, tmp_path, capsys):
    res = xfiles.run("tv-en-1995-mpxfiles", out=tmp_path / "run", capsys=capsys)
    rows = res["rows"]
    assert len(rows) == 31
    assert {(r["status"], r["account"], r["default_account"], r["proof"]) for r in rows} == {
        ("wrong-account", "movies", "tv", "filename")}
    checks = {r["manual_id"]: r["override_check"] for r in rows}
    assert [checks[m] for m in XFILES[:29]] == ["covers"] * 29
    assert (checks[XFILES[29]], checks[XFILES[30]]) == ("none", "differs")
    out = res["stdout"]
    assert ("Wrong account (IMP-C26): 31 object(s) live in another account than their id prefix — mvconfig "
            "override covers 29 · none 1 · override points elsewhere 1 · learned home already 0") in out
    assert f"     {XFILES[30]} · whole — override anime, item in movies" in out


def test_several_items_with_the_name_in_the_default_account_are_ambiguous(world, tmp_path):
    world.save().upload_all()
    world.inv.add("movies", world.names[(M_WHOLE, "whole")], taken="2025-01-01T00:00:00Z")    # other bytes
    world.save()
    row = by_key(world.run(M_WHOLE, out=tmp_path / "run"))[(M_WHOLE, "whole")]
    assert (row["status"], row["item"], len(row["candidates"])) == ("ambiguous", None, 2)
    assert {c["account"] for c in row["candidates"]} == {"movies"}


@pytest.mark.parametrize("same", [True, False])
def test_a_name_in_two_other_accounts(world, tmp_path, same):
    world.save().upload_all(skip={T_EP1})
    name = world.names[(T_EP1, "whole")]
    a = world.inv.add("movies", name, data=b"EP1 bytes")
    b = world.inv.add("anime", name, data=b"EP1 bytes" if same else b"other bytes")
    world.save()
    row = by_key(world.run(T_EP1, out=tmp_path / "run"))[(T_EP1, "whole")]
    if same:     # one object, two copies: the first account (GP order, override / home first) + a recorded copy
        assert (row["status"], row["account"], row["item"]["photo_id"]) == ("wrong-account", "movies", a["photo_id"])
        assert [c["photo_id"] for c in row["copies"]] == [b["photo_id"]]
    else:
        assert (row["status"], row["item"]) == ("ambiguous", None)
        assert {c["photo_id"] for c in row["candidates"]} == {a["photo_id"], b["photo_id"]}


def test_an_extra_copy_with_the_same_bytes_is_recorded_other_bytes_only_noted(world, tmp_path):
    """PLAN P-4 (the Kuroko copies in TV): the default account's item is the object's; a same-bytes copy elsewhere
    is recorded, a same-named item with other bytes is not."""
    world.save().upload_all(skip={A_EP, M_WHOLE})
    a = world.inv.add("anime", world.names[(A_EP, "whole")], data=b"anime ep")
    copy_ = world.inv.add("tv", world.names[(A_EP, "whole")], data=b"anime ep")
    world.inv.add("movies", world.names[(M_WHOLE, "whole")], data=b"movie")
    world.inv.add("tv", world.names[(M_WHOLE, "whole")], data=b"a re-encode")
    world.save()
    rows = by_key(world.run(out=tmp_path / "run"))
    anime = rows[(A_EP, "whole")]
    assert (anime["status"], anime["account"], anime["item"]["photo_id"]) == ("mapped", "anime", a["photo_id"])
    assert [c["photo_id"] for c in anime["copies"]] == [copy_["photo_id"]] and anime["copies"][0]["account"] == "tv"
    movie = rows[(M_WHOLE, "whole")]
    assert movie["copies"] == [] and "a same-named item with other bytes is also in tv (not recorded)" in movie["note"]


def test_evidence_from_a_stale_inventory_is_refused(world, tmp_path):
    world.inv.enumerated["movies"] = NOW - timedelta(hours=30)
    world.save().upload_all(skip={T_EP1})
    world.inv.add("movies", world.names[(T_EP1, "whole")])
    world.save()
    rows = by_key(world.run(out=tmp_path / "run"))
    row = rows[(T_EP1, "whole")]
    assert row["status"] == "stale-inventory" and row["item"] is None
    assert row["note"].startswith("movies: the name is in this account, whose inventory is not fresh")
    assert rows[(T_EP2, "whole")]["status"] == "mapped"                   # found in its own fresh account


@pytest.mark.parametrize("case", ["stale", "gaps"])
def test_in_no_account_needs_every_crawled_inventory_fresh_and_detailed(world, tmp_path, case):
    world.save().upload_all(skip={T_EP1})
    if case == "stale":
        world.inv.enumerated["anime"] = NOW - timedelta(hours=30)
    else:
        world.inv.undetailed["anime"].append("AF1QipFAKEundetailed0001")       # listed, never detailed
    world.save()
    row = by_key(world.run(T_EP1, out=tmp_path / "run"))[(T_EP1, "whole")]
    assert row["status"] == "stale-inventory"
    why = "the enumeration is 30.0 h old" if case == "stale" else "1 known id(s) without details — re-run the crawl"
    assert row["note"].startswith("anime: not in any account, but that cannot be told") and why in row["note"]
    world.inv.enumerated["anime"], world.inv.undetailed["anime"] = ENUMERATED, []
    world.save()
    assert by_key(world.run(T_EP1, out=tmp_path / "again"))[(T_EP1, "whole")]["status"] == "missing-everywhere"


def test_the_short_id_tag_gives_a_probable_match_of_the_same_kind_and_chunk(world, tmp_path):
    """OD-14: only the `[short_id]` tag matches (e.g. the file was renamed after its push) -> probable, never
    applied without the user."""
    world.save().upload_all(skip={M_WHOLE, (M_SPLIT, "chunk 2/3")})
    sid_whole, sid_split = world.library[M_WHOLE]["short_id"], world.library[M_SPLIT]["short_id"]
    renamed = world.inv.add("movies", f"Renamed Title 2019 [{sid_whole}].mkv")
    world.inv.add("movies", f"Renamed Title 2019 [{sid_whole}].chunk.001.mkv")       # another kind: no
    chunk = world.inv.add("tv", f"Old Split Name [{sid_split}].chunk.002.mkv")          # another account
    world.inv.add("movies", f"Old Split Name [{sid_split}].chunk.003.mkv")              # another chunk: no
    world.save()
    rows = by_key(world.run(out=tmp_path / "run"))
    whole = rows[(M_WHOLE, "whole")]
    assert (whole["status"], whole["account"], whole["name_match"]) == ("probable", "movies", "tag")
    assert [c["photo_id"] for c in whole["candidates"]] == [renamed["photo_id"]] and whole["item"] is None
    two = rows[(M_SPLIT, "chunk 2/3")]
    assert (two["status"], two["account"]) == ("probable", "tv")
    assert [c["photo_id"] for c in two["candidates"]] == [chunk["photo_id"]]


def test_a_possibly_deleted_item_never_matches_and_is_reported(world, tmp_path):
    world.save().upload_all()
    gone = next(r for r in world.inv.items["tv"] if r["filename"] == world.names[(T_EP1, "whole")])
    world.inv.unlisted["tv"].add(gone["photo_id"])                 # listed before, absent from the latest enumeration
    world.save()
    row = by_key(world.run(T_EP1, out=tmp_path / "run"))[(T_EP1, "whole")]
    assert (row["status"], row["item"]) == ("missing-everywhere", None)   # (the World's device check: complete)
    assert [d["photo_id"] for d in row["deleted"]] == [gone["photo_id"]]
    assert "absent from the latest enumeration (deleted?)" in row["note"]


def test_an_override_pointing_away_from_the_item_is_flagged(world, tmp_path, monkeypatch, capsys):
    world.save().upload_all().save()
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"fetch_account_overrides": {T_EP1: "anime"}})
    res = world.run(T_EP1, out=tmp_path / "run", capsys=capsys)
    row = by_key(res)[(T_EP1, "whole")]
    assert (row["status"], row["account"], row["override_check"]) == ("mapped", "tv", "differs")
    assert "⚠️  mvconfig fetch_account_overrides routes 1 object(s) away from the account that holds them:" in res["stdout"]


# ============================================================================ 9.3 the content key (SHA-1 = dedupKey)

PUSHED = "2026-09-30T10:00:00Z"                      # before the enumeration: the inventories stay fresh


def _sha1(data):
    return hashlib.sha1(data).hexdigest()


def test_a_captured_object_is_proven_by_its_dedup_key(world, tmp_path):
    world.save().upload_all(skip={M_WHOLE})
    item = world.inv.add("movies", world.names[(M_WHOLE, "whole")], data=b"the whole movie")
    world.save()
    world.capture(M_WHOLE, _h(M_WHOLE), _sha1(b"the whole movie"), PUSHED, world.names[(M_WHOLE, "whole")])
    row = by_key(world.run(M_WHOLE, out=tmp_path / "run"))[(M_WHOLE, "whole")]
    assert (row["status"], row["proof"], row["name_match"]) == ("mapped", "dedupkey", "exact")
    assert row["item"]["photo_id"] == item["photo_id"] and row["content_key"] == _key(b"the whole movie")
    assert row["cloud_state"] == "unverified"


def test_a_same_named_item_with_other_bytes_is_a_conflict_never_a_match(world, tmp_path):
    """PLAN Step 9 (3): capture sha1 -> dedupKey must equal the item's, else `conflict` (never applied)."""
    world.save().upload_all(skip={M_WHOLE})
    other = world.inv.add("movies", world.names[(M_WHOLE, "whole")], data=b"some other bytes")
    world.save()
    world.capture(M_WHOLE, _h(M_WHOLE), _sha1(b"the whole movie"), PUSHED, world.names[(M_WHOLE, "whole")])
    row = by_key(world.run(M_WHOLE, out=tmp_path / "run"))[(M_WHOLE, "whole")]
    assert (row["status"], row["item"], row["proof"]) == ("conflict", None, None)
    assert [c["photo_id"] for c in row["candidates"]] == [other["photo_id"]]
    assert "none holds the object's bytes (dedupKey)" in row["note"]


def test_the_bytes_under_another_name_are_proven_and_flagged_for_the_names_repair(world, tmp_path):
    world.save().upload_all(skip={M_WHOLE})
    item = world.inv.add("movies", "Something Renamed On The Phone.mkv", data=b"the whole movie")
    world.save()
    world.capture(M_WHOLE, _h(M_WHOLE), _sha1(b"the whole movie"), PUSHED, world.names[(M_WHOLE, "whole")])
    row = by_key(world.run(M_WHOLE, out=tmp_path / "run"))[(M_WHOLE, "whole")]
    assert (row["status"], row["proof"], row["name_match"]) == ("mapped", "dedupkey", "content")
    assert row["item"]["photo_id"] == item["photo_id"]
    assert "uploaded under another name (Something Renamed On The Phone.mkv) — --repair names" in row["note"]


def test_content_proof_in_another_account_outranks_a_same_named_item(world, tmp_path):
    world.save().upload_all(skip={T_EP1})
    world.inv.add("tv", world.names[(T_EP1, "whole")], data=b"a different encode")
    proven = world.inv.add("movies", world.names[(T_EP1, "whole")], data=b"episode one")
    copy_ = world.inv.add("anime", "whatever.mkv", data=b"episode one")
    world.save()
    world.capture(T_EP1, _h(T_EP1), _sha1(b"episode one"), PUSHED, world.names[(T_EP1, "whole")])
    row = by_key(world.run(T_EP1, out=tmp_path / "run"))[(T_EP1, "whole")]
    assert (row["status"], row["account"], row["proof"]) == ("wrong-account", "movies", "dedupkey")
    assert row["item"]["photo_id"] == proven["photo_id"]
    assert [c["photo_id"] for c in row["copies"]] == [copy_["photo_id"]]       # the same bytes, recorded
    assert "tv: 1 item(s) carry this name, none holds the object's bytes" in row["note"]


def test_a_trusted_block_s_content_key_counts_like_a_capture(world, tmp_path):
    entry = world.library[T_EP2]
    entry["gphotos"] = mvcommon.new_gphotos_block(entry["hash"], sha1=_sha1(b"episode two"))
    world.save().upload_all(skip={T_EP2})
    world.inv.add("tv", world.names[(T_EP2, "whole")], data=b"not episode two")
    world.save()
    row = by_key(world.run(T_EP2, out=tmp_path / "run"))[(T_EP2, "whole")]
    assert (row["status"], row["content_key"]) == ("conflict", _key(b"episode two"))
    entry["gphotos"] = mvcommon.new_gphotos_block(_h("other bytes: untrusted"), sha1=_sha1(b"episode two"))
    world.save()                                                  # an UNTRUSTED block (P-2) is ignored
    row = by_key(world.run(T_EP2, out=tmp_path / "run2"))[(T_EP2, "whole")]
    assert (row["status"], row["content_key"], row["proof"]) == ("mapped", None, "filename")


def test_the_same_bytes_twice_is_one_item_and_an_alternate(world, tmp_path):
    world.save().upload_all(skip={M_WHOLE})
    later = world.inv.add("movies", world.names[(M_WHOLE, "whole")], data=b"movie", upload_ms=1_790_000_000_000)
    first = world.inv.add("movies", world.names[(M_WHOLE, "whole")], data=b"movie", upload_ms=1_780_000_000_000)
    world.save()
    world.capture(M_WHOLE, _h(M_WHOLE), _sha1(b"movie"), PUSHED, world.names[(M_WHOLE, "whole")])
    row = by_key(world.run(M_WHOLE, out=tmp_path / "run"))[(M_WHOLE, "whole")]
    assert (row["status"], row["item"]["photo_id"], row["duplicate"]) == ("mapped", first["photo_id"], "same-bytes")
    assert row["alternates"] == [later["photo_id"]]


def test_a_content_keyed_object_found_nowhere_is_never_probable(world, tmp_path):
    world.save().upload_all(skip={M_WHOLE})
    world.inv.add("movies", f"Renamed [{world.library[M_WHOLE]['short_id']}].mkv", data=b"other bytes")
    world.save()
    world.capture(M_WHOLE, _h(M_WHOLE), _sha1(b"the movie"), PUSHED, world.names[(M_WHOLE, "whole")])
    row = by_key(world.run(M_WHOLE, out=tmp_path / "run"))[(M_WHOLE, "whole")]
    assert (row["status"], row["candidates"]) == ("missing-everywhere", [])


# ============================================================================ 9.4 duplicates and re-split sets

SEC_A, SEC_B, SEC_C = "2024-03-09T06:03:02Z", "2024-06-01T10:00:00Z", "2024-08-15T20:30:45Z"
SPLIT_CHUNKS = [(M_SPLIT, f"chunk {i}/3") for i in (1, 2, 3)]


def _uploads(world, sets, label_names=None):
    """Upload M_SPLIT's chunks once per (taken, [chunk numbers]) in `sets` -> {(taken, n): record}."""
    out = {}
    for taken, numbers in sets:
        for n in numbers:
            name = world.names[SPLIT_CHUNKS[n - 1]]
            out[(taken, n)] = world.inv.add("movies", name, taken=taken)
    return out


def _split_world(world, sets):
    world.save().upload_all(skip=set(SPLIT_CHUNKS))
    world.save()
    items = _uploads(world, sets)
    world.save()
    return items


def test_a_re_split_uploaded_twice_maps_to_the_newest_complete_set(world, tmp_path):
    """The 7 real duplicated chunk names: an older upload of 2 of the chunks + a newer complete upload."""
    items = _split_world(world, [(SEC_A, [1, 2]), (SEC_B, [1, 2, 3])])
    rows = by_key(world.run(M_SPLIT, out=tmp_path / "run"))
    for n, key in enumerate(SPLIT_CHUNKS, 1):
        row = rows[key]
        assert (row["status"], row["proof"], row["duplicate"]) == ("mapped", "filename", "re-split"), key
        assert row["item"]["photo_id"] == items[(SEC_B, n)]["photo_id"]
        assert row["alternates"] == ([items[(SEC_A, n)]["photo_id"]] if n < 3 else [])
        assert row["set"] == {"taken_s": _ms(SEC_B) // 1000, "uploads": 2, "chunks": 3}
    assert rows[(M_SPLIT, "holder")]["status"] == "mapped"          # the holder is not part of the chunk set


def test_three_uploads_with_one_complete_and_newest(world, tmp_path):
    items = _split_world(world, [(SEC_A, [2]), (SEC_B, [2]), (SEC_C, [1, 2, 3])])
    rows = by_key(world.run(M_SPLIT, out=tmp_path / "run"))
    assert [rows[k]["item"]["photo_id"] for k in SPLIT_CHUNKS] == [items[(SEC_C, n)]["photo_id"] for n in (1, 2, 3)]
    assert sorted(rows[SPLIT_CHUNKS[1]]["alternates"]) == sorted([items[(SEC_A, 2)]["photo_id"],
                                                                  items[(SEC_B, 2)]["photo_id"]])


@pytest.mark.parametrize("sets,fragment", [
    ([(SEC_A, [1, 2, 3]), (SEC_B, [1])], "a newer upload of this split is incomplete (2 uploads"),
    ([(SEC_A, [1, 2]), (SEC_B, [2, 3])], "this split was uploaded 2 times and no upload is complete"),
], ids=["newer-upload-incomplete", "no-complete-upload"])
def test_an_unresolvable_re_split_is_ambiguous_for_the_user(world, tmp_path, sets, fragment):
    """Never map the chunks to an upload that may hold other bytes than the library's split: a newer upload that is
    still incomplete (perhaps still backing up) blocks the older complete one."""
    _split_world(world, sets)
    rows = by_key(world.run(M_SPLIT, out=tmp_path / "run"))
    for key in SPLIT_CHUNKS:
        if rows[key]["status"] == "not-found":
            continue                                   # a chunk with no item at all (case 2 has all three)
        assert (rows[key]["status"], rows[key]["item"]) == ("ambiguous", None), key
        assert fragment in rows[key]["note"] and rows[key]["candidates"]


def test_one_upload_maps_each_chunk_and_flags_two_byte_versions_at_one_second(world, tmp_path):
    items = _split_world(world, [(SEC_A, [1, 2, 3])])
    twin = world.inv.add("movies", world.names[SPLIT_CHUNKS[0]], taken=SEC_A, data=items[(SEC_A, 1)]["photo_id"].encode())
    same = world.inv.add("movies", world.names[SPLIT_CHUNKS[1]], taken=SEC_A, upload_ms=_ms(SEC_A) + 2 * 86_400_000,
                         data=b"x")
    world.inv.items["movies"][-1]["dedup_key"] = items[(SEC_A, 2)]["dedup_key"]      # the same bytes, twice
    world.save()
    rows = by_key(world.run(M_SPLIT, out=tmp_path / "run"))
    assert rows[SPLIT_CHUNKS[0]]["status"] == "ambiguous" and twin["photo_id"] in {
        c["photo_id"] for c in rows[SPLIT_CHUNKS[0]]["candidates"]}
    two = rows[SPLIT_CHUNKS[1]]
    assert (two["status"], two["item"]["photo_id"], two["alternates"], two["duplicate"]) == (
        "mapped", items[(SEC_A, 2)]["photo_id"], [same["photo_id"]], "same-bytes")
    assert rows[SPLIT_CHUNKS[2]]["status"] == "mapped"


def test_a_content_proven_chunk_pins_its_split_s_upload(world, tmp_path):
    items = _split_world(world, [(SEC_A, [1, 2, 3])])
    newer = {n: world.inv.add("movies", world.names[SPLIT_CHUNKS[n - 1]], taken=SEC_B, data=f"new {n}".encode())
             for n in (1, 2)}
    world.save()
    chunk = world.library[M_SPLIT]["split_info"]["chunks"][0]
    world.capture(M_SPLIT, chunk["hash"], _sha1(b"new 1"), PUSHED, chunk["filename"], role="chunk", index=1)
    rows = by_key(world.run(M_SPLIT, out=tmp_path / "run"))
    one, two, three = (rows[k] for k in SPLIT_CHUNKS)
    assert (one["status"], one["proof"], one["item"]["photo_id"]) == ("mapped", "dedupkey", newer[1]["photo_id"])
    assert (two["status"], two["proof"], two["item"]["photo_id"]) == ("mapped", "filename", newer[2]["photo_id"])
    assert "pinned by a chunk proven by its bytes" in two["note"] and two["alternates"] == []
    assert (three["status"], three["item"]) == ("conflict", None)            # only the OLD upload carries its name
    assert [c["photo_id"] for c in three["candidates"]] == [items[(SEC_A, 3)]["photo_id"]]


def test_an_extras_split_item_is_its_own_set(world, tmp_path):
    keys = [(M_SPLIT, f"extra Specials/Making Of.mkv chunk {n}/2") for n in (1, 2)]
    world.save().upload_all(skip=set(keys))
    world.save()
    old = world.inv.add("movies", world.names[keys[0]], taken=SEC_A)
    new = [world.inv.add("movies", world.names[k], taken=SEC_B) for k in keys]
    world.save()
    rows = by_key(world.run(M_SPLIT, out=tmp_path / "run"))
    assert [rows[k]["item"]["photo_id"] for k in keys] == [n["photo_id"] for n in new]
    assert rows[keys[0]]["alternates"] == [old["photo_id"]] and rows[keys[0]]["duplicate"] == "re-split"
    assert all(rows[k]["status"] == "mapped" for k in SPLIT_CHUNKS)   # the leaf's own chunks are another set


def test_the_set_rule_engages_only_for_different_bytes_duplicates(world, tmp_path):
    """PLAN Step 9 (4) is about duplicates: a split whose chunk names are unique maps chunk by chunk even if its
    items carry different seconds (only noted — the real library has none: 250/250 such sets on one second)."""
    items = _split_world(world, [(SEC_A, [1]), (SEC_B, [2]), (SEC_C, [3])])
    rows = by_key(world.run(M_SPLIT, out=tmp_path / "run"))
    for n, key in enumerate(SPLIT_CHUNKS, 1):
        assert (rows[key]["status"], rows[key]["item"]["photo_id"]) == ("mapped", items[(SEC_A if n == 1 else
                                                                          SEC_B if n == 2 else SEC_C, n)]["photo_id"])
        assert "carry 3 different taken seconds (mixed uploads?)" in rows[key]["note"]


def test_a_whole_file_uploaded_twice_with_the_same_bytes_maps_once(world, tmp_path):
    world.save().upload_all(skip={M_WHOLE})
    first = world.inv.add("movies", world.names[(M_WHOLE, "whole")], data=b"m", upload_ms=1_780_000_000_000)
    again = world.inv.add("movies", world.names[(M_WHOLE, "whole")], data=b"m", upload_ms=1_790_000_000_000)
    world.save()
    row = by_key(world.run(M_WHOLE, out=tmp_path / "run"))[(M_WHOLE, "whole")]
    assert (row["status"], row["proof"], row["item"]["photo_id"]) == ("mapped", "filename", first["photo_id"])
    assert (row["alternates"], row["duplicate"]) == ([again["photo_id"]], "same-bytes")


# ============================================================================ 9.5 IMP-C27 detectors and repairs

T_WIRE = "tv-en-2003-mpwire-s02e12"        # the library says one whole file; Google Photos holds its 2 chunks


def _split_lost(world, sidecars=(1, 2), seconds=(SEC_A, SEC_A), holder=False, numbers=(1, 2)):
    """T_WIRE: a whole tv episode whose chunks (and maybe a FLAC holder) are in the tv account; the push's local
    checksums/<chunk>.sha256 sidecars for the chunk numbers in `sidecars` -> {number: (chunk name, sha256)}."""
    folder = world.folder("Series", "MP Wire", "Season 02")
    world.library[T_WIRE] = leaf(T_WIRE, folder, "MP.Wire.S02E12.mkv")
    world.save().upload_all(skip={T_WIRE})
    stem = os.path.splitext(world.names[(T_WIRE, "whole")])[0]
    chunks = {}
    for n, taken in zip(numbers, seconds):
        name = f"{stem}.chunk.{n:03d}.mkv"
        world.inv.add("tv", name, taken=taken)
        chunks[n] = (name, _h(f"wire chunk {n}"))
    if holder:
        world.inv.add("tv", f"{stem}.holder.mkv", taken=SEC_A)
    os.makedirs(folder / "checksums", exist_ok=True)
    for n in sidecars:
        name, sha = chunks[n]
        (folder / "checksums" / f"{name}.sha256").write_text(f"{sha} *{name}", encoding="utf-8")
    world.save()
    return chunks


def test_split_info_lost_is_detected_and_proven_from_the_local_checksums(world, tmp_path):
    chunks = _split_lost(world)
    row = by_key(world.run(T_WIRE, out=tmp_path / "run"))[(T_WIRE, "whole")]
    assert (row["status"], row["issue"], row["account"], row["name_match"]) == (
        "inconsistent", "split-info-lost", "tv", "chunks")
    plan = row["repair"]
    assert (plan["kind"], plan["provable"], plan["why"], plan["sha256"]) == ("split-info", True, None, _h(T_WIRE))
    assert plan["split_info"] == {"is_split": True, "total_chunks": 2, "chunks": [
        {"filename": chunks[1][0], "hash": chunks[1][1]}, {"filename": chunks[2][0], "hash": chunks[2][1]}]}
    assert plan["slot"] == [T_WIRE] and len(plan["photo_ids"]) == 2


@pytest.mark.parametrize("variant,why", [
    (dict(sidecars=(1,)), "no local checksum sidecar for chunk(s) [2]"),
    (dict(seconds=(SEC_A, SEC_B)), "its chunks were uploaded more than once"),
    (dict(holder=True), "a FLAC holder was carried out"),
    (dict(numbers=(1, 3), sidecars=(1, 3)), "not 1..2 exactly once"),
])
def test_split_info_that_cannot_be_proven_stays_a_user_decision(world, tmp_path, variant, why):
    _split_lost(world, **variant)
    row = by_key(world.run(T_WIRE, out=tmp_path / "run"))[(T_WIRE, "whole")]
    assert (row["status"], row["issue"], row["repair"]["provable"], row["repair"]["split_info"]) == (
        "inconsistent", "split-info-lost", False, None)
    assert why in row["repair"]["why"]


def test_the_split_info_writer_repairs_only_the_unchanged_whole_file(world, tmp_path):
    chunks = _split_lost(world)
    plan = by_key(world.run(T_WIRE, out=tmp_path / "run"))[(T_WIRE, "whole")]["repair"]
    before = mvcommon.load_library()
    report = main._mp_write_split_info([plan])
    assert (report["status"], report["repaired"], report["skipped"], report["attempts"]) == (
        "saved", [{"manual_id": T_WIRE, "label": "whole", "chunks": 2}], [], 1)
    after = mvcommon.load_library()
    assert after[T_WIRE]["split_info"] == plan["split_info"]
    assert {k: v for k, v in after[T_WIRE].items() if k != "split_info"} == before[T_WIRE]   # nothing else
    assert {k: v for k, v in after.items() if k != T_WIRE} == {k: v for k, v in before.items() if k != T_WIRE}
    rows = by_key(world.run(T_WIRE, out=tmp_path / "again"))                 # now its chunks map, like any split
    assert [(k[1], r["status"]) for k, r in rows.items()] == [("chunk 1/2", "mapped"), ("chunk 2/2", "mapped")]
    assert [r["item"]["filename"] for r in rows.values()] == [chunks[1][0], chunks[2][0]]
    again = main._mp_write_split_info([plan])                                   # it is split now: skipped
    assert (again["status"], again["skipped"]) == ("up-to-date", [{"manual_id": T_WIRE, "label": "whole",
                                                                   "why": "it is split already"}])


def test_the_split_info_writer_honours_the_pin_and_unprovable_plans(world, tmp_path, monkeypatch):
    _split_lost(world)
    plan = by_key(world.run(T_WIRE, out=tmp_path / "run"))[(T_WIRE, "whole")]["repair"]
    library = mvcommon.load_library()
    library[T_WIRE]["hash"] = _h("re-mastered")                                  # the object changed since
    mvcommon.save_library(library)
    report = main._mp_write_split_info([plan])
    assert report["status"] == "up-to-date" and report["skipped"][0]["why"].startswith("the object changed")
    assert "split_info" not in mvcommon.load_library()[T_WIRE]
    assert main._mp_write_split_info([dict(plan, provable=False)])["status"] == "nothing-to-apply"
    saves = []
    monkeypatch.setattr(main, "save_library", lambda data: saves.append(1))
    assert main._mp_write_split_info([])["status"] == "nothing-to-apply" and saves == []


def test_the_split_info_writer_reloads_when_the_library_changes_under_it(world, tmp_path, monkeypatch):
    _split_lost(world)
    plan = by_key(world.run(T_WIRE, out=tmp_path / "run"))[(T_WIRE, "whole")]["repair"]
    real, loads = main.load_library, []

    def racing():
        library = real()
        loads.append(1)
        if len(loads) == 1:                           # another MediaVault process saves after our first load
            other = mvcommon.load_library()
            other[T_EP1]["status"] = "restored_local"
            mvcommon.save_library(other)
        return library

    monkeypatch.setattr(main, "load_library", racing)
    report = main._mp_write_split_info([plan])
    assert (report["status"], report["attempts"]) == ("saved", 2)
    after = mvcommon.load_library()
    assert after[T_EP1]["status"] == "restored_local" and after[T_WIRE]["split_info"]["total_chunks"] == 2


def test_an_old_untagged_upload_name_is_found_and_flagged_for_the_names_repair(world, tmp_path):
    """IMP-C27 (b) (Fanaa): pushed before uploads were tagged, so Google Photos knows the plain file name."""
    world.save().upload_all(skip={M_WHOLE})
    plain = world.inv.add("movies", world.library[M_WHOLE]["filename"])
    world.save()
    row = by_key(world.run(M_WHOLE, out=tmp_path / "run"))[(M_WHOLE, "whole")]
    assert (row["status"], row["issue"], row["name_match"], row["proof"]) == (
        "inconsistent", "untagged-name", "untagged", "filename")
    assert row["item"]["photo_id"] == plain["photo_id"]
    assert row["repair"] == {"kind": "names", "uploaded_name": world.library[M_WHOLE]["filename"]}


def test_an_untagged_name_two_objects_share_belongs_to_neither(world, tmp_path):
    twin = "tv-en-2016-mpother-s01e01"
    world.library[twin] = leaf(twin, world.folder("Series", "MP Other"), "MP.Show.S02E01.mkv")   # T_EP1's name
    world.save().upload_all(skip={T_EP1, twin})
    world.inv.add("tv", "MP.Show.S02E01.mkv")
    world.save()
    rows = by_key(world.run("tv-en", out=tmp_path / "run"))
    for key in ((T_EP1, "whole"), (twin, "whole")):
        assert (rows[key]["status"], rows[key]["item"], rows[key]["repair"]) == ("ambiguous", None, None), key
        assert "2 library objects share this old untagged name" in rows[key]["note"]


@pytest.mark.parametrize("flag,issue", [({"partial_upload": True}, "partial-upload"),
                                        ({"original_quality": False}, "not-original-quality")])
def test_a_partial_or_non_original_upload_is_inconsistent_never_applied(world, tmp_path, flag, issue):
    world.save().upload_all(skip={M_WHOLE})
    world.inv.add("movies", world.names[(M_WHOLE, "whole")], **flag)
    world.save()
    row = by_key(world.run(M_WHOLE, out=tmp_path / "run"))[(M_WHOLE, "whole")]
    assert (row["status"], row["issue"]) == ("inconsistent", issue) and row["item"] is not None
    assert "a restore would fail its SHA-256 check" in row["note"]


def _sidecar(world, key):
    return main._dev_sidecar_name(world.names[key], world.library[key[0]]["short_id"])


def test_objects_in_no_account_get_their_device_verdict_and_a_loud_block(world, tmp_path, capsys):
    gone = [(T_EP1, "whole"), (T_EP2, "whole"), (M_WHOLE, "whole"), (M_SPLIT, "chunk 3/3")]
    world.save().upload_all(skip=set(gone))
    world.save()
    world.on_phone("series", world.names[(T_EP1, "whole")])                            # still on a phone
    world.on_phone("series", _sidecar(world, (T_EP2, "whole")))                        # freed after its backup
    world.on_phone("movies", _sidecar(world, (M_SPLIT, "chunk 3/3")))                  # (shared by the split)
    res = world.run(out=tmp_path / "run", capsys=capsys)
    rows = by_key(res)
    assert rows[(T_EP1, "whole")]["status"] == "awaiting-backup"
    assert rows[(T_EP2, "whole")]["status"] == "missing-after-backup"
    assert rows[(M_WHOLE, "whole")]["status"] == "missing-everywhere"
    three = rows[(M_SPLIT, "chunk 3/3")]
    assert (three["status"], three["partial_set"]) == ("missing-after-backup", True)    # chunks 1-2 ARE in GP
    assert not rows[(M_SPLIT, "chunk 1/3")]["partial_set"]
    assert three["device"]["state"] == "freed" and three["device"]["sidecars"][0]["alias"] == "movies"
    assert world.device_calls == 1 and res["devices"]["complete"] is True
    out = res["stdout"]
    assert "⚠️⚠️⚠️  NOT IN GOOGLE PHOTOS (IMP-C27): 4 object(s)" in out
    assert "  missing-after-backup: 2 — in no account and freed from a phone" in out
    assert "  partial chunk sets: 1 object(s) in 1 entr(y/ies)" in out
    assert f"    missing-everywhere       {M_WHOLE} · whole — on no phone" in out
    assert f"    missing-after-backup     {M_SPLIT} · chunk 3/3 [partial set] — sidecar only on movies (FAKEMOV0001)" in out
    assert f"    awaiting-backup          {T_EP1} · whole — on series (FAKESER0002)" in out


def test_an_incomplete_device_check_never_calls_anything_missing(world, tmp_path, capsys):
    world.save().upload_all(skip={(T_EP1, "whole")})
    world.save()
    world.devices["complete"] = False
    world.devices["devices"]["movies"].update(status="not-connected", detail="adb devices does not list it", files=[])
    world.devices["unconfigured"] = [{"serial": "FAKEUNCONF09", "state": "device"}]
    res = world.run(out=tmp_path / "run", capsys=capsys)
    row = by_key(res)[(T_EP1, "whole")]
    assert row["status"] == "device-check-incomplete" and "missing" not in row["status"]
    assert "not listed: movies (FAKEMOV0001) not-connected — connect it and re-run" in row["note"]
    out = res["stdout"]
    assert "INCOMPLETE — nothing is called missing-everywhere" in out
    assert "  movies    FAKEMOV0001        not-connected — adb devices does not list it" in out
    assert "⚠️  attached but not configured: FAKEUNCONF09 (device)" in out


def test_no_device_check_runs_when_every_object_is_somewhere(world, tmp_path):
    world.save().upload_all().save()
    res = world.run(out=tmp_path / "run")
    assert res["devices"] is None and world.device_calls == 0


def test_the_device_check_lists_the_extra_movies_phone(world, tmp_path, monkeypatch):
    """DECISIONS §1d through the real device_inventory (read-only fake adb): an object on the EXTRA movies phone is
    awaiting backup; with every configured phone listed the check is complete."""
    from test_device_inventory import FakeAdb, Phone
    monkeypatch.setattr(main, "device_inventory", REAL_DEVICE_INVENTORY)
    monkeypatch.setattr(main, "DEVICE_ALIASES", {"movies": "FAKEMOV0001", "series": "FAKESER0002",
                                                 "others": "<NEW_PIXEL_SERIAL>"})
    monkeypatch.setattr(main, "DEVICE_CHECK_EXTRA_SERIALS", {"movies": ["FAKEXTRA0003"]})
    world.save().upload_all(skip={M_WHOLE})
    world.save()
    fake = FakeAdb({"FAKEMOV0001": Phone(files={}), "FAKESER0002": Phone(files={}),
                    "FAKEXTRA0003": Phone(files={"Movies/x/" + world.names[(M_WHOLE, "whole")]: (5, 1)})})
    monkeypatch.setattr(main.subprocess, "run", fake.run)
    res = world.run(M_WHOLE, out=tmp_path / "run")
    row = by_key(res)[(M_WHOLE, "whole")]
    assert row["status"] == "awaiting-backup" and row["device"]["copies"][0]["alias"] == "movies#2"
    assert res["devices"]["complete"] is True and fake.violations == []


# ============================================================================ 9.5 the apply engine: --repair names

def test_the_engine_records_a_proven_upload_name(world):
    world.save()
    sha = world.library[M_WHOLE]["hash"]
    names = [{"manual_id": M_WHOLE, "sha256": sha, "uploaded_name": "MP.Whole.2019.1080p.mkv"}]
    preview = main.gphotos_apply([], names=names)                          # dry run: the diff, nothing written
    assert (preview["status"], preview["records"], preview["objects"]) == ("dry-run", 0, 1)
    assert preview["changed"][0]["fields"]["uploaded_name"] == [None, "MP.Whole.2019.1080p.mkv"]
    assert "gphotos" not in mvcommon.load_library()[M_WHOLE]
    report = main.gphotos_apply([], dry_run=False, names=names)
    assert report["status"] == "saved"
    block = mvcommon.load_library()[M_WHOLE]["gphotos"]
    assert block == {"v": 1, "sha256": sha, "uploaded_name": "MP.Whole.2019.1080p.mkv"}
    obj = next(mvcommon.iter_uploaded_objects(mvcommon.load_library(), [M_WHOLE]))
    assert obj["uploaded_name"] == "MP.Whole.2019.1080p.mkv"          # what every later step now searches for


def test_a_name_repair_wins_over_the_capture_and_bad_facts_are_counted(world):
    world.save()
    sha = world.library[T_EP1]["hash"]
    world.capture(T_EP1, sha, _sha1(b"ep"), PUSHED, "Predicted [x].mkv")
    names = [{"manual_id": T_EP1, "sha256": sha, "uploaded_name": "Actual Name.mkv"},
             {"manual_id": T_EP1, "sha256": "nope", "uploaded_name": "x.mkv"},
             {"manual_id": T_EP1, "sha256": sha, "uploaded_name": "a/b.mkv"},
             {"manual_id": "tv-en-1999-gone-s01e01", "sha256": sha, "uploaded_name": "Ghost.mkv"},
             {"manual_id": T_EP1, "sha256": _h("re-mastered"), "uploaded_name": "Old.mkv"}]
    report = main.gphotos_apply([], dry_run=False, names=names)
    assert (report["status"], report["invalid"]) == ("saved", 2)
    assert mvcommon.load_library()[T_EP1]["gphotos"]["uploaded_name"] == "Actual Name.mkv"
    assert [(o["manual_id"], o["account"], o["reason"]) for o in report["orphans"]] == [
        ("tv-en-1999-gone-s01e01", None, "no-entry"), (T_EP1, None, "no-object")]
    assert main.gphotos_apply([], names=[names[1]])["status"] == "nothing-to-apply"


# ============================================================================ 9.6 the unmatched inventory report

def _read_csv(path):
    raw = open(path, "rb").read()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    import csv as _csv
    import io as _io
    return list(_csv.DictReader(_io.StringIO(raw.decode("utf-8-sig"), newline="")))


def test_every_unused_inventory_item_is_categorized_never_touched(world, tmp_path, capsys):
    chunk = world.library[M_SPLIT]["split_info"]["chunks"][0]
    world.save().upload_all(skip={(M_SPLIT, "chunk 1/3"), M_WHOLE})
    world.inv.add("movies", chunk["filename"], data=b"chunk one", taken=SEC_B)          # the split's own upload
    old_chunk = world.inv.add("movies", chunk["filename"], taken=SEC_A)                  # an older upload of it
    world.inv.add("movies", world.names[(M_WHOLE, "whole")], data=b"movie")
    dup = world.inv.add("movies", world.names[(M_WHOLE, "whole")], data=b"a re-encode")
    renamed = world.inv.add("movies", f"Old Name [{world.library[M_WHOLE]['short_id']}].mkv")
    old_split = world.inv.add("movies", f"Old Name [{world.library[M_WHOLE]['short_id']}].chunk.001.mkv")
    cross = world.inv.add("tv", world.names[(M_WHOLE, "whole")], data=b"tv copy, other bytes")
    unknown = world.inv.add("movies", "Removed Entry 2001 [abcdef].mkv")
    phone = world.inv.add("anime", "PXL_20260101_123456789.mp4")
    gone = world.inv.add("tv", "Deleted Holiday Clip.mp4")
    world.inv.unlisted["tv"].add(gone["photo_id"])
    broken = world.inv.add("tv", "half.mkv", error="incomplete: batch info")
    world.save()
    world.capture(M_SPLIT, chunk["hash"], _sha1(b"chunk one"), PUSHED, chunk["filename"], role="chunk", index=1)
    world.capture(M_WHOLE, _h(M_WHOLE), _sha1(b"movie"), PUSHED, world.names[(M_WHOLE, "whole")])
    res = world.run(out=tmp_path / "run", capsys=capsys)
    got = {(acc, u["item"]["photo_id"]): (u["category"], u["owner"], u["note"])
           for acc, found in res["unmatched"].items() for u in found}
    assert got == {
        ("movies", old_chunk["photo_id"]): ("superseded-split", f"{M_SPLIT} · chunk 1/3", None),
        ("movies", dup["photo_id"]): ("duplicate", f"{M_WHOLE} · whole", None),
        ("movies", renamed["photo_id"]): ("duplicate", f"{M_WHOLE} · whole",
                                          "carries the object's [short_id] tag under another name"),
        ("movies", old_split["photo_id"]): ("superseded-split", f"{M_WHOLE} · whole",
                                            "carries the object's [short_id] tag under another name"),
        ("movies", unknown["photo_id"]): ("mediavault-unknown", None, None),
        ("tv", cross["photo_id"]): ("cross-account-copy", f"{M_WHOLE} · whole", "the object lives in movies"),
        ("tv", gone["photo_id"]): ("possibly-deleted", None, None),
        ("tv", broken["photo_id"]): ("errored", None, None),
        ("anime", phone["photo_id"]): ("non-mediavault", None, None),
    }
    assert set(res["unmatched"]) == {"movies", "tv", "anime"}                  # others: never crawled
    rows = _read_csv(os.path.join(res["run_dir"], "unmatched_movies.csv"))
    assert list(rows[0]) == ["category", "photo_id", "filename", "taken_utc", "upload_utc", "owner", "note"]
    assert [r["category"] for r in rows] == ["superseded-split", "superseded-split", "duplicate", "duplicate",
                                             "mediavault-unknown"]
    assert {r["taken_utc"] for r in rows} == {"2024-03-09 06:03:02 UTC"}
    assert ("Unmatched inventory items (report only — never touched): movies 5 (superseded-split 2 · duplicate 2 · "
            "mediavault-unknown 1)") in res["stdout"]


def test_the_unmatched_report_needs_the_whole_library(world, tmp_path, capsys):
    world.save().upload_all().save()
    for tokens in ((T_SEASON,), ("--account", "tv")):
        res = world.run(*tokens, out=tmp_path / "-".join(tokens), capsys=capsys)
        assert res["unmatched"] is None
        assert "Unmatched inventory items: reported by `map_photos all` (without --account) only" in res["stdout"]
        assert not [f for f in os.listdir(res["run_dir"]) if f.startswith("unmatched_")]


def test_a_stale_account_gets_no_unmatched_report(world, tmp_path):
    world.inv.enumerated["anime"] = NOW - timedelta(hours=48)
    world.save().upload_all()
    world.inv.add("anime", "PXL_20260101_123456789.mp4")
    world.save()
    res = world.run(out=tmp_path / "run")
    assert set(res["unmatched"]) == {"movies", "tv"} and res["unmatched"]["movies"] == []


# ============================================================================ 9.7 outputs: resolutions_todo.csv & co

@pytest.fixture()
def mixed(world):
    """One object per unresolved class: probable, ambiguous, conflict, untagged (inconsistent), a lost member of a
    partial split (partial-set), missing-everywhere; plus awaiting-backup, wrong-account and mapped (resolved)."""
    world.save().upload_all(skip={M_WHOLE, T_EP1, T_EP2, A_EP, (M_SPLIT, "chunk 3/3"),
                                  (M_SPLIT, "extra Specials/BTS.mkv")})
    world.inv.add("movies", f"Renamed [{world.library[M_WHOLE]['short_id']}].mkv")              # probable
    world.inv.add("tv", world.names[(T_EP1, "whole")], taken=SEC_A)                               # ambiguous
    world.inv.add("tv", world.names[(T_EP1, "whole")], taken=SEC_B)
    world.inv.add("tv", world.names[(T_EP2, "whole")], data=b"other bytes")                      # conflict
    world.inv.add("anime", world.library[A_EP]["filename"])                                       # untagged
    world.inv.add("movies", world.names[(M_SPLIT, "extra Specials/BTS.mkv")].replace("BTS", "BTS"), data=b"bts")
    world.save()
    world.capture(T_EP2, _h(T_EP2), _sha1(b"episode two"), PUSHED, world.names[(T_EP2, "whole")])
    world.on_phone("movies", _sidecar(world, (M_SPLIT, "chunk 3/3")))                             # freed
    return world


def test_resolutions_todo_lists_every_unresolved_object_in_the_failed_list_format(mixed, tmp_path, capsys):
    res = mixed.run(out=tmp_path / "run", capsys=capsys)
    rows = by_key(res)
    assert {k: r["reason"] for k, r in rows.items() if r["reason"]} == {
        (M_WHOLE, "whole"): "probable", (T_EP1, "whole"): "ambiguous", (T_EP2, "whole"): "wrong-item",
        (A_EP, "whole"): "inconsistent", (M_SPLIT, "chunk 3/3"): "partial-set"}
    path = os.path.join(res["run_dir"], "resolutions_todo.csv")
    csv_rows = _read_csv(path)
    assert tuple(csv_rows[0]) == mvcommon.GP_FAILED_CSV_COLUMNS
    by_obj = {(r["manual_id"], r["object"]): r for r in csv_rows}
    assert set(by_obj) == {k for k, r in rows.items() if r["reason"]}
    ambiguous = by_obj[(T_EP1, "whole")]
    assert ambiguous["status"] == "ambiguous" and ambiguous["suggested_photo_url"] == ""      # 2 candidates
    assert ambiguous["candidates"].startswith("exact_name=ambiguous(2) | candidates: AF1QipFAKE")
    probable = by_obj[(M_WHOLE, "whole")]
    assert probable["suggested_photo_url"].startswith("https://photos.google.com/photo/AF1QipFAKE")
    assert probable["set_search_cmd"] == f'python main.py set_search {M_WHOLE} "<new search term>"'
    assert by_obj[(A_EP, "whole")]["status"] == "inconsistent: untagged-name"
    assert by_obj[(M_SPLIT, "chunk 3/3")]["status"] == "missing-after-backup (partial set)"
    conflict = by_obj[(T_EP2, "whole")]
    assert conflict["expected_taken_local"] == "2024-03-09 14:03:02 GMT+08:00"            # DateUTC + the phone's tz
    assert conflict["sha256"] == _h(T_EP2) and conflict["decision"] == ""
    assert mvcommon.read_failed_csv(path) == ([], [])                                      # nothing decided yet
    assert ("To resolve: 5 row(s) in resolutions_todo.csv (ambiguous 1 · wrong-item 1 · probable 1 · partial-set 1 · "
            "inconsistent 1)") in res["stdout"]
    assert f"python main.py map_photos all --resolutions <that csv>" in res["stdout"]


def test_the_failed_records_follow_p18(mixed, tmp_path):
    res = mixed.run(out=tmp_path / "run")
    records = {(r["manual_id"], r["label"]): r for r in res["todo"]}
    conflict = records[(T_EP2, "whole")]
    assert conflict["tried"] == [{"approach": "exact_name", "outcome": "wrong-item", "n": 1}]
    assert conflict["run"] == "map-20261001T120000Z" and conflict["account"] == "tv" and conflict["category"] == "tv"
    assert records[(M_SPLIT, "chunk 3/3")]["tried"] == [{"approach": "exact_name", "outcome": "not-found", "n": 0}]
    assert "candidates" not in records[(M_SPLIT, "chunk 3/3")]
    for record in res["todo"]:
        assert mvcommon._normalize_failed(record, fill=False) == record                  # append_failed-valid


def test_inconsistencies_csv_lists_the_imp_c27_rows(mixed, tmp_path):
    res = mixed.run(out=tmp_path / "run")
    rows = _read_csv(os.path.join(res["run_dir"], "inconsistencies.csv"))
    assert list(rows[0]) == ["account", "category", "manual_id", "object", "uploaded_name", "status", "issue",
                             "partial_set", "device", "repair", "note"]
    got = {(r["manual_id"], r["object"]): (r["status"], r["issue"], r["partial_set"], r["device"]) for r in rows}
    assert got == {(T_EP2, "whole"): ("conflict", "", "", ""), (A_EP, "whole"): ("inconsistent", "untagged-name", "", ""),
                   (M_SPLIT, "chunk 3/3"): ("missing-after-backup", "", "yes", "freed")}
    untagged = next(r for r in rows if r["issue"] == "untagged-name")
    assert untagged["repair"] == f"--repair names records '{world_name(mixed, A_EP)}'"


def world_name(world, mid):
    return world.library[mid]["filename"]


def test_an_edited_todo_in_a_reused_out_dir_is_never_overwritten(mixed, tmp_path, capsys):
    out = tmp_path / "run"
    mixed.run(out=out)
    path = out / "resolutions_todo.csv"
    rows = _read_csv(path)
    rows[0].update(decision="skip")
    mvcommon.write_failed_csv(str(path), rows)
    edited = path.read_bytes()
    res = mixed.run(out=out, capsys=capsys)
    assert path.read_bytes() == edited
    assert (out / "resolutions_todo.new.csv").exists()
    assert f"⚠️  {path} holds your edits — kept; this run's list is resolutions_todo.new.csv" in res["stdout"]
    assert "resolutions_todo.new.csv" in res["stdout"].splitlines()[-1]


def test_the_outputs_line_names_every_file(world, tmp_path, capsys):
    world.save().upload_all().save()
    res = world.run(out=tmp_path / "run", capsys=capsys)
    last = res["stdout"].splitlines()[-1]
    assert last == (f"Outputs: {tmp_path / 'run'} — summary.txt, mapping.jsonl, mapping.csv, inconsistencies.csv, "
                    f"resolutions_todo.csv, unmatched_movies.csv, unmatched_tv.csv, unmatched_anime.csv")
    assert sorted(os.listdir(res["run_dir"])) == sorted(last.split(" — ")[1].split(", "))


def test_mapping_csv_lists_the_wrong_account_block_with_its_override_coverage(xfiles, tmp_path):
    """🚦 R1 reads the wrong-account list here: one row per object, the account that holds it, whether an mvconfig
    override covers it — and no photo id or dedupKey (those stay in mapping.jsonl)."""
    res = xfiles.run(out=tmp_path / "run")
    path = os.path.join(res["run_dir"], "mapping.csv")
    rows = _read_csv(path)
    assert list(rows[0]) == ["account", "category", "manual_id", "object", "uploaded_name", "status", "issue",
                             "name_match", "proof", "gp_filename", "taken_utc", "upload_utc", "duplicate",
                             "alternates", "copies", "default_account", "override", "override_check",
                             "learned_home", "device", "partial_set", "user_decision", "note"]
    assert [(r["manual_id"], r["object"], r["status"]) for r in rows] == [
        (r["manual_id"], r["label"], r["status"]) for r in res["rows"]]                  # every object, in order
    wrong = {r["manual_id"]: r for r in rows if r["status"] == "wrong-account"}
    assert sorted(wrong) == XFILES
    first = wrong[XFILES[0]]
    assert (first["account"], first["category"], first["default_account"], first["override"],
            first["override_check"]) == ("movies", "tv", "tv", "movies", "covers")
    assert (first["uploaded_name"], first["gp_filename"]) == (xfiles.names[(XFILES[0], "whole")],) * 2
    assert (first["taken_utc"], first["upload_utc"]) == ("2024-03-09 06:03:02 UTC", "2024-03-10 06:03:02 UTC")
    assert (wrong[XFILES[29]]["override"], wrong[XFILES[29]]["override_check"]) == ("", "none")
    assert (wrong[XFILES[30]]["override"], wrong[XFILES[30]]["override_check"]) == ("anime", "differs")
    text = open(path, encoding="utf-8-sig").read()
    for account in mvcommon.GP_ACCOUNTS:
        for rec in xfiles.inv.items[account]:
            assert rec["photo_id"] not in text and rec["dedup_key"] not in text
    assert "AF1Qip" not in text


def test_mapping_csv_shows_near_matches_duplicates_and_copies(world, tmp_path):
    world.save().upload_all(skip={M_WHOLE, T_EP1, (M_SPLIT, "chunk 3/3")})
    name, episode = world.names[(M_WHOLE, "whole")], world.names[(T_EP1, "whole")]
    world.inv.add("movies", name.upper())                              # a near-match (P-6)
    for account in ("tv", "tv", "movies"):                             # the same bytes twice + a copy in movies
        world.inv.add(account, episode, data=b"episode one")
    world.save()
    world.on_phone("movies", _sidecar(world, (M_SPLIT, "chunk 3/3")))  # freed: a lost member of a partial set
    res = world.run(out=tmp_path / "run")
    rows = {(r["manual_id"], r["object"]): r for r in _read_csv(os.path.join(res["run_dir"], "mapping.csv"))}
    near = rows[(M_WHOLE, "whole")]
    assert (near["status"], near["name_match"], near["uploaded_name"], near["gp_filename"]) == (
        "mapped-normalized", "normalized", name, name.upper())
    twice = rows[(T_EP1, "whole")]
    assert (twice["status"], twice["duplicate"], twice["alternates"], twice["copies"]) == (
        "mapped", "same-bytes", "1", "movies")
    lost = rows[(M_SPLIT, "chunk 3/3")]
    assert (lost["status"], lost["gp_filename"], lost["device"], lost["partial_set"]) == (
        "missing-after-backup", "", "freed", "yes")
    plain = rows[(A_EP, "whole")]
    assert (plain["duplicate"], plain["alternates"], plain["copies"], plain["partial_set"], plain["note"]) == ("",) * 5


@pytest.mark.parametrize("tokens,tail", [
    ((), "and re-run."),
    (("--apply",), "and re-run (what --apply recorded stays on record; a re-run records nothing twice)."),
])
def test_an_unwritable_report_file_ends_the_run_with_one_line(mixed, tmp_path, monkeypatch, capsys, tokens, tail):
    """A report of a re-used --out that is open elsewhere (Excel) must not end the run — least of all an --apply —
    in a traceback: one ❌ line, status error; what the apply recorded stays, and the re-run records nothing twice."""
    real = main._mp_write_csv

    def locked(path, header, rows):
        if os.path.basename(path) == "inconsistencies.csv":
            raise PermissionError(13, "Permission denied", path)
        return real(path, header, rows)

    monkeypatch.setattr(main, "_mp_write_csv", locked)
    res = mixed.run(*tokens, out=tmp_path / "run", capsys=capsys)
    assert res["status"] == "error"
    (line,) = [text for text in res["stdout"].splitlines() if text.startswith("❌ map_photos:")]
    assert line.startswith(f"❌ map_photos: the reports could not be written to {tmp_path / 'run'} (PermissionError:")
    assert "inconsistencies.csv" in line and line.endswith(tail)
    assert bool(mvcommon.read_learned()) == bool(tokens)                 # recorded by the apply, never by a dry run
    monkeypatch.setattr(main, "_mp_write_csv", real)
    again = mixed.run(*tokens, out=tmp_path / "run", capsys=capsys)
    assert again["status"] == "ok" and "❌ map_photos:" not in again["stdout"]
    if tokens:
        assert again["applied"]["learned"][0] == 0 and again["applied"]["learned"][1] > 0


# ============================================================================ 9.8 --resolutions and the hints store

HINT = {"account": "tv", "manual_id": T_EP1, "label": "whole", "sha256": _h(T_EP1)}


@pytest.mark.parametrize("record", [
    dict(HINT, kind="url", photo_id="AF1QipFAKEhint0001xxxxxxxxxx"),
    dict(HINT, kind="datetime", taken_ms=1_644_061_786_000, gmt_offset_min=480, taken_local="2022-02-05T19:49:46"),
])
def test_the_hints_store_round_trips(record, monkeypatch):
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: NOW)
    written = mvcommon.append_hint(record)
    assert (written["v"], written["source"], written["at"]) == (1, "resolutions", "2026-10-01T12:00:00Z")
    assert mvcommon.read_hints() == [written]
    assert mvcommon.hints_path().startswith(mvcommon.GPHOTOS_STATE_DIR)


@pytest.mark.parametrize("record,fragment", [
    (dict(HINT, kind="url"), "a url hint names the item's photo_id"),
    (dict(HINT, kind="url", photo_id="AF1QipFAKEhint0001xx", taken_ms=1), "a url hint carries no time"),
    (dict(HINT, kind="datetime", taken_ms=1, gmt_offset_min=900, taken_local="2022-02-05T19:49:46"), "gmt_offset_min"),
    (dict(HINT, kind="datetime", taken_ms=1, gmt_offset_min=0, taken_local="5 Feb"), "taken_local"),
    (dict(HINT, kind="voice"), "kind must be one of"),
    (dict(HINT, kind="url", photo_id="AF1QipFAKEhint0001xx", account="films"), "account must be one of"),
    (dict(HINT, kind="url", photo_id="AF1QipFAKEhint0001xx", sha256="short"), "sha256"),
    (dict(HINT, kind="url", photo_id="AF1QipFAKEhint0001xx", extra=1), "unknown key"),
])
def test_the_hints_store_refuses_bad_records(record, fragment):
    with pytest.raises(ValueError, match=fragment):
        mvcommon.append_hint(record)
    assert not os.path.exists(mvcommon.hints_path())


def _decide(path, decisions):
    """Fill the decision columns of a todo / failed CSV: {(manual_id, object): {column: value}}."""
    rows = _read_csv(path)
    for row in rows:
        row.update(decisions.get((row["manual_id"], row["object"]), {}))
    mvcommon.write_failed_csv(str(path), rows)
    return path


def _filled(mixed, tmp_path):
    first = mixed.run(out=tmp_path / "first")
    url = next(r["suggested_photo_url"] for r in _read_csv(os.path.join(first["run_dir"], "resolutions_todo.csv"))
               if r["manual_id"] == M_WHOLE)
    return _decide(os.path.join(first["run_dir"], "resolutions_todo.csv"), {
        (M_WHOLE, "whole"): {"decision": "url", "photo_url": url},
        (T_EP1, "whole"): {"decision": "datetime", "date": "2022-02-05", "time": "7:49:46 PM", "gmt": "+08:00"},
        (T_EP2, "whole"): {"decision": "search", "new_search_term": "MP Show S02E02 director's cut"},
        (M_SPLIT, "chunk 3/3"): {"decision": "not_uploaded", "note": "never pushed this chunk"},
        (A_EP, "whole"): {"decision": "skip"}}), url


def test_resolutions_are_validated_and_previewed_by_a_dry_run(mixed, tmp_path, capsys):
    path, url = _filled(mixed, tmp_path)
    capsys.readouterr()
    before, state = library_files(mixed.sandbox), {k: v for k, v in state_files().items() if not k.startswith("map")}
    res = mixed.run("--resolutions", str(path), out=tmp_path / "second", capsys=capsys)
    assert library_files(mixed.sandbox) == before
    assert {k: v for k, v in state_files().items() if not k.startswith("map")} == state      # nothing recorded
    rows = by_key(res)
    assert {k: r["user_decision"] for k, r in rows.items() if r["user_decision"]} == {
        (M_WHOLE, "whole"): "url", (T_EP1, "whole"): "datetime", (T_EP2, "whole"): "search",
        (M_SPLIT, "chunk 3/3"): "not_uploaded", (A_EP, "whole"): "skip"}
    todo = _read_csv(os.path.join(res["run_dir"], "resolutions_todo.csv"))
    assert [(r["manual_id"], r["object"]) for r in todo] == [(A_EP, "whole")]           # only the skipped one stays
    out = res["stdout"]
    assert (f"Resolutions ({path}): 5 decision(s) — url 1 · datetime 1 · search 1 · not_uploaded 1 · skip 1") in out
    assert "  dry run — nothing recorded: add --apply to record them" in out
    assert f'Next: python main.py map_photos all --resolutions "{path}" --apply' in out   # the previewed decisions
    assert "⚠️⚠️⚠️  YOU SAY NOT UPLOADED (IMP-C27): 1 object(s)" in out
    assert f"    {M_SPLIT} · chunk 3/3 (mapper verdict: missing-after-backup)" in out
    incons = {(r["manual_id"], r["object"]): r["issue"] for r in _read_csv(os.path.join(res["run_dir"],
                                                                                       "inconsistencies.csv"))}
    assert incons[(M_SPLIT, "chunk 3/3")] == "user says not uploaded"


def test_resolutions_build_validated_records(mixed, tmp_path):
    path, url = _filled(mixed, tmp_path)
    library = mvcommon.load_library()
    objects = list(mvcommon.iter_uploaded_objects(library))
    res = main._mp_resolutions(str(path), library, objects)
    assert res["errors"] == [] and res["out_of_scope"] == 0 and res["fatal"] is None
    pid = url.rsplit("/", 1)[1]
    (url_record, search_record) = res["learned"]
    assert {k: url_record[k] for k in ("account", "manual_id", "label", "photo_id", "proof", "source")} == {
        "account": "movies", "manual_id": M_WHOLE, "label": "whole", "photo_id": pid, "proof": "manual",
        "source": "manual"}
    assert (search_record["search_override"], search_record["source"], search_record["account"]) == (
        "MP Show S02E02 director's cut", "manual", "tv")
    (url_hint, time_hint) = res["hints"]
    assert (url_hint["kind"], url_hint["photo_id"]) == ("url", pid)
    assert (time_hint["kind"], time_hint["taken_local"], time_hint["gmt_offset_min"], time_hint["taken_ms"]) == (
        "datetime", "2022-02-05T19:49:46", 480, 1_644_061_786_000)


def test_bad_resolution_rows_are_listed_and_never_imported(mixed, tmp_path, capsys):
    path, url = _filled(mixed, tmp_path)
    rows = _read_csv(path)
    rows.append(dict(rows[0], decision="url", photo_url="not a link", object="whole", sha256=""))
    rows.append(dict(rows[0], manual_id="tv-en-1999-gone-s01e01", decision="skip", sha256=""))
    rows.append(dict(rows[0], decision="skip", sha256=_h("bytes before a re-split")))
    mvcommon.write_failed_csv(str(path), rows)
    res = mixed.run(T_SEASON, "--resolutions", str(path), out=tmp_path / "scoped", capsys=capsys)
    out = res["stdout"]
    assert "decision(s) — datetime 1 · search 1 · out of this run's scope" in out
    assert "· invalid 3" in out
    assert "❌ line" in out and "decision url needs photo_url" in out
    assert "no such library id" in out and "is no longer in the library (re-split or re-mastered?)" in out


def test_an_unreadable_resolutions_file_imports_nothing(mixed, tmp_path, capsys):
    bad = tmp_path / "notes.csv"
    bad.write_text("name;size\nx;1\n", encoding="utf-8")
    res = mixed.run("--resolutions", str(bad), out=tmp_path / "run", capsys=capsys)
    assert f"❌ --resolutions {bad}: ValueError: notes.csv is not a failed-objects / resolutions CSV" in res["stdout"]
    assert all(r["user_decision"] is None for r in res["rows"])
    (next_line,) = [line for line in res["stdout"].splitlines() if line.startswith("Next: ")]
    assert next_line == "Next: python main.py map_photos all --apply"    # the command to copy never names the bad file


def test_objects_resolved_earlier_leave_the_todo_list(mixed, tmp_path):
    library = mvcommon.load_library()
    library[M_WHOLE]["gphotos"] = mvcommon.new_gphotos_block(library[M_WHOLE]["hash"], accounts={"movies": {
        "photo_id": "AF1QipFAKEmanual0001xxxxxxxx", "verified_by": "manual", "source": "manual",
        "verified_at": "2026-09-30T10:00:00Z"}})
    mvcommon.save_library(library)
    mvcommon.append_hint(dict(HINT, kind="datetime", taken_ms=1, gmt_offset_min=480, taken_local="2022-02-05T19:49:46"))
    res = mixed.run(out=tmp_path / "run")
    rows = by_key(res)
    assert (rows[(M_WHOLE, "whole")]["user_decision"], rows[(T_EP1, "whole")]["user_decision"]) == (
        "url (recorded)", "datetime (recorded)")
    todo = {(r["manual_id"], r["object"]) for r in _read_csv(os.path.join(res["run_dir"], "resolutions_todo.csv"))}
    assert (M_WHOLE, "whole") not in todo and (T_EP1, "whole") not in todo and (T_EP2, "whole") in todo
    assert rows[(M_WHOLE, "whole")]["reason"] == "probable"            # still unresolved until check_photos hinted


# ============================================================================ 9.9 --apply

def _blocks(library):
    """{(manual_id, label): trusted block} of every uploaded object."""
    return {(o["manual_id"], o["label"]): o["block"] for o in mvcommon.iter_uploaded_objects(library)}


def _without_gphotos(node):
    """`node` with every nested `gphotos` key removed."""
    if isinstance(node, dict):
        return {k: _without_gphotos(v) for k, v in node.items() if k != "gphotos"}
    if isinstance(node, list):
        return [_without_gphotos(v) for v in node]
    return node


def test_a_dry_run_previews_the_apply_and_writes_nothing(world, tmp_path, capsys):
    world.save().upload_all().save()
    before = library_files(world.sandbox)
    res = world.run(out=tmp_path / "run", capsys=capsys)
    preview = res["preview"]
    assert (preview["status"], preview["dry_run"], preview["saved"]) == ("dry-run", True, False)
    assert len(preview["changed"]) == preview["confirmed"] == 12        # every mapped object of the 3 crawled accounts
    assert library_files(world.sandbox) == before
    assert not [p for p in state_files() if not p.startswith("map")] and not os.path.exists(mvcommon.learned_path("tv"))
    out = res["stdout"]
    assert "Apply preview (dry run — nothing written):" in out
    assert "  library (dry-run): 12 object(s) would change (12 newly confirmed)" in out
    assert "Next: python main.py map_photos all --apply" in out


def test_apply_records_learned_facts_and_confirms_the_mapped_objects(xfiles, tmp_path, capsys):
    """PLAN Step 9 (9): the accepted rows go to the learned sidecar (source map) — the durable record — and the apply
    engine folds them into the library: mapped objects confirmed (filename proof), the X-Files-like block homed in
    the account that holds them (P-4), nothing else of the library touched."""
    before = mvcommon.load_library()
    res = xfiles.run("--apply", out=tmp_path / "run", capsys=capsys)
    applied = res["applied"]
    assert applied["error"] is None and applied["engine"]["status"] == "saved"
    library = mvcommon.load_library()
    blocks = _blocks(library)
    episode = blocks[(XFILES[0], "whole")]
    assert episode["home_account"] == "movies"
    record = episode["accounts"]["movies"]
    assert (record["verified_by"], record["source"], record["confirmed"]) == ("filename", "map", True)
    assert record["gp_filename"] == xfiles.names[(XFILES[0], "whole")] and "approach" not in record
    assert blocks[(T_EP1, "whole")]["accounts"]["tv"]["confirmed"] is True
    assert blocks[(O_MATCH, "whole")] is None                                    # stale (never crawled): untouched
    learned = mvcommon.read_learned("movies")
    assert {r["manual_id"] for r in learned} >= set(XFILES) and {r["source"] for r in learned} == {"map"}
    assert json.dumps(_without_gphotos(library)) == json.dumps(_without_gphotos(before))   # only blocks changed
    out = res["stdout"]
    assert out.startswith("=== MAP PHOTOS (APPLY) — scope: all")
    assert "  library (saved): " in out and "Recorded: learned records " in out


def test_a_second_apply_records_nothing_twice(mixed, tmp_path, capsys):
    first = mixed.run("--apply", out=tmp_path / "a")["applied"]
    assert first["learned"][0] > 0 and first["failed"][0] == 5
    sizes = {p: os.path.getsize(os.path.join(mvcommon.GPHOTOS_STATE_DIR, p))
             for p in os.listdir(mvcommon.GPHOTOS_STATE_DIR) if p.endswith(".jsonl")}
    before = library_files(mixed.sandbox)
    second = mixed.run("--apply", out=tmp_path / "b")["applied"]
    assert (second["learned"], second["failed"]) == ((0, first["learned"][0]), (0, 5))
    assert second["engine"]["status"] == "up-to-date" and library_files(mixed.sandbox) == before
    assert {p: os.path.getsize(os.path.join(mvcommon.GPHOTOS_STATE_DIR, p)) for p in sizes} == sizes


def test_apply_puts_the_unresolved_rows_on_the_failed_list(mixed, tmp_path):
    res = mixed.run("--apply", out=tmp_path / "run")
    failed = mvcommon.read_failed()
    assert {(r["manual_id"], r["label"], r["reason"]) for r in failed} == {
        (M_WHOLE, "whole", "probable"), (T_EP1, "whole", "ambiguous"), (T_EP2, "whole", "wrong-item"),
        (A_EP, "whole", "inconsistent"), (M_SPLIT, "chunk 3/3", "partial-set")}
    assert {r["run"] for r in failed} == {"map-20261001T120000Z"}
    csv_rows = _read_csv(mvcommon.failed_csv_path())                     # regenerated (P-18)
    assert len(csv_rows) == 5 and res["applied"]["failed_csv"]["rows"] == 5
    blocks = _blocks(mvcommon.load_library())                            # nothing unresolved was applied
    for key in ((M_WHOLE, "whole"), (T_EP1, "whole"), (T_EP2, "whole"), (A_EP, "whole"), (M_SPLIT, "chunk 3/3")):
        assert blocks[key] is None or not any(r.get("confirmed") for r in blocks[key].get("accounts", {}).values())


@pytest.mark.parametrize("flag,issue", [({"partial_upload": True}, "partial-upload"),
                                        ({"original_quality": False}, "not-original-quality")])
def test_a_partial_or_non_original_copy_is_never_applied(world, tmp_path, flag, issue):
    world.save().upload_all(skip={M_WHOLE})
    world.inv.add("movies", world.names[(M_WHOLE, "whole")], **flag)
    world.save()
    world.run("--apply", out=tmp_path / "run")
    assert _blocks(mvcommon.load_library())[(M_WHOLE, "whole")] is None
    assert [r["reason"] for r in mvcommon.read_failed()] == ["inconsistent"]


def test_repair_names_records_the_real_upload_name(world, tmp_path):
    world.save().upload_all(skip={M_WHOLE})
    world.inv.add("movies", world.library[M_WHOLE]["filename"])
    world.save()
    world.run("--apply", out=tmp_path / "plain")                     # without the repair: not applied
    assert _blocks(mvcommon.load_library())[(M_WHOLE, "whole")] is None
    res = world.run("--apply", "--repair", "names", out=tmp_path / "names")
    block = _blocks(mvcommon.load_library())[(M_WHOLE, "whole")]
    assert block["uploaded_name"] == world.library[M_WHOLE]["filename"]
    assert block["accounts"]["movies"]["verified_by"] == "filename" and block["accounts"]["movies"]["confirmed"]
    again = by_key(world.run(M_WHOLE, out=tmp_path / "again"))[(M_WHOLE, "whole")]
    assert (again["status"], again["name_match"]) == ("mapped", "exact")       # the library knows its real name
    assert res["applied"]["engine"]["status"] == "saved"


def test_repair_split_info_writes_the_proven_split_and_maps_its_chunks_in_the_same_run(world, tmp_path):
    chunks = _split_lost(world)
    res = world.run(T_WIRE, "--apply", "--repair", "split-info", out=tmp_path / "run")
    split = res["applied"]["split_info"]
    assert (split["status"], split["repaired"]) == ("saved", [{"manual_id": T_WIRE, "label": "whole", "chunks": 2}])
    library = mvcommon.load_library()
    assert _without_gphotos(library[T_WIRE]["split_info"]) == {"is_split": True, "total_chunks": 2, "chunks": [
        {"filename": chunks[n][0], "hash": chunks[n][1]} for n in (1, 2)]}
    rows = by_key(res)
    assert [(k[1], r["status"]) for k, r in rows.items()] == [("chunk 1/2", "mapped"), ("chunk 2/2", "mapped")]
    blocks = _blocks(library)
    assert all(blocks[(T_WIRE, f"chunk {n}/2")]["accounts"]["tv"]["confirmed"] for n in (1, 2))
    assert "gphotos" not in library[T_WIRE]                                    # P-3: never an entry-level block


def test_repair_split_info_without_proof_writes_nothing(world, tmp_path):
    _split_lost(world, sidecars=(1,))
    before = mvcommon.load_library()[T_WIRE]
    res = world.run(T_WIRE, "--apply", "--repair", "split-info", out=tmp_path / "run")
    assert res["applied"]["split_info"]["status"] == "nothing-to-apply"
    assert mvcommon.load_library()[T_WIRE] == before


def test_accept_probable_records_a_manual_match_that_never_confirms(world, tmp_path):
    world.save().upload_all(skip={M_WHOLE})
    renamed = world.inv.add("movies", f"Renamed Title 2019 [{world.library[M_WHOLE]['short_id']}].mkv")
    world.save()
    world.run("--apply", out=tmp_path / "plain")
    assert _blocks(mvcommon.load_library())[(M_WHOLE, "whole")] is None       # OD-14: never without the user
    world.run("--apply", "--accept-probable", out=tmp_path / "accepted")
    record = _blocks(mvcommon.load_library())[(M_WHOLE, "whole")]["accounts"]["movies"]
    assert (record["photo_id"], record["verified_by"], record.get("confirmed")) == (renamed["photo_id"], "manual", None)


def test_resolutions_are_recorded_by_apply(mixed, tmp_path):
    path, url = _filled(mixed, tmp_path)
    res = mixed.run("--resolutions", str(path), "--apply", out=tmp_path / "second")
    assert res["applied"]["hints"][0] == 2                                      # url + datetime
    hints = mvcommon.read_hints()
    assert {(h["manual_id"], h["kind"]) for h in hints} == {(M_WHOLE, "url"), (T_EP1, "datetime")}
    blocks = _blocks(mvcommon.load_library())
    movie = blocks[(M_WHOLE, "whole")]["accounts"]["movies"]
    assert (movie["photo_id"], movie["verified_by"], movie.get("confirmed")) == (url.rsplit("/", 1)[1], "manual", None)
    assert blocks[(T_EP2, "whole")]["search_override"]["query"] == "MP Show S02E02 director's cut"
    failed = {(r["manual_id"], r["label"]) for r in mvcommon.read_failed()}
    assert failed == {(A_EP, "whole")}                       # decided rows are not listed; the skipped one stays


def test_an_imported_failed_objects_csv_is_regenerated_only_when_consumed(mixed, tmp_path):
    mixed.run("--apply", out=tmp_path / "a")                                    # puts 5 rows on the failed list
    csv_path = mvcommon.failed_csv_path()
    _decide(csv_path, {(T_EP2, "whole"): {"decision": "search", "new_search_term": "MP Show two"}})
    res = mixed.run("--resolutions", csv_path, "--apply", out=tmp_path / "b")
    assert res["applied"]["failed_csv"]["rows"] == 5                         # consumed -> regenerated
    assert all(r["decision"] == "" for r in _read_csv(csv_path))
    _decide(csv_path, {(T_EP1, "whole"): {"decision": "url", "photo_url": "not a link"}})     # an invalid edit
    res = mixed.run("--resolutions", csv_path, "--apply", out=tmp_path / "c")
    assert res["applied"]["failed_csv"] == {"path": csv_path, "kept": True, "edited_rows": 1}
    assert "  failed_objects.csv: kept — it holds 1 edited row(s)" in open(
        os.path.join(res["run_dir"], "summary.txt"), encoding="utf-8").read()


def test_an_edited_failed_objects_csv_survives_a_plain_apply(mixed, tmp_path):
    mixed.run("--apply", out=tmp_path / "a")
    csv_path = mvcommon.failed_csv_path()
    _decide(csv_path, {(T_EP2, "whole"): {"decision": "skip", "note": "later"}})
    edited = open(csv_path, "rb").read()
    res = mixed.run("--apply", out=tmp_path / "b")
    assert open(csv_path, "rb").read() == edited
    assert res["applied"]["failed_csv"]["kept"] is True


def test_a_write_failure_stops_before_the_library(mixed, tmp_path, monkeypatch, capsys):
    def full_disk(account, record):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(mvcommon, "append_learned", full_disk)
    before = library_files(mixed.sandbox)
    res = mixed.run("--apply", out=tmp_path / "run", capsys=capsys)
    assert res["applied"]["engine"] is None and library_files(mixed.sandbox) == before
    assert "❌ map_photos --apply stopped: OSError: [Errno 28] No space left on device — the library was not changed" \
           in res["stdout"]


def test_apply_never_opens_a_rollback_journal(world, tmp_path, monkeypatch):
    """OD-3 / the rollback change-gate: map_photos (like the apply engine) never touches the rollback journal."""
    class Forbidden:
        def __init__(self, *_a, **_k):
            raise AssertionError("map_photos must never open a RollbackJournal")

    monkeypatch.setattr(main, "RollbackJournal", Forbidden)
    _split_lost(world)
    res = world.run("--apply", "--repair", "split-info,names", out=tmp_path / "run")
    assert res["applied"]["engine"]["status"] == "saved"
    assert not [p for p in tmp_path.rglob("*") if p.name == main.TXN_JOURNAL_NAME]


# ============================================================================ 9.10 --devices-only

def test_devices_only_is_just_the_read_only_device_report(world, tmp_path, monkeypatch, capsys):
    """§M2: which objects are still on a Pixel (awaiting backup) vs freed — no inventory, no Google Photos, nothing
    written but the run directory."""
    world.save()
    world.on_phone("series", world.names[(T_EP1, "whole")])
    world.on_phone("movies", _sidecar(world, (M_WHOLE, "whole")))
    monkeypatch.setattr(main, "_mp_inventory_states", lambda: pytest.fail("--devices-only must not read the inventory"))
    before, state = library_files(world.sandbox), state_files()
    res = world.run("--devices-only", out=tmp_path / "run", capsys=capsys)
    assert library_files(world.sandbox) == before and state_files() == state
    states = {(o["manual_id"], o["label"]): o["state"] for o in res["objects"]}
    assert states[(T_EP1, "whole")] == "on_device" and states[(M_WHOLE, "whole")] == "freed"
    assert states[(O_MATCH, "whole")] == "absent"
    out = res["stdout"]
    assert out.startswith("=== MAP PHOTOS — device check only (read-only) — scope: all — 13 object(s) ===")
    assert "Verdicts: on_device 1 · freed 1 · absent 11" in out
    assert f"    {T_EP1} · whole — on series (FAKESER0002)" in out
    assert "Freed from a phone (1) — only the .mvmeta.json sidecar is left" in out
    assert sorted(os.listdir(res["run_dir"])) == ["devices.csv", "summary.txt"]
    rows = {(r["manual_id"], r["object"]): r for r in _read_csv(os.path.join(res["run_dir"], "devices.csv"))}
    assert rows[(T_EP1, "whole")]["state"] == "on_device" and "FAKESER0002" in rows[(T_EP1, "whole")]["copies"]
    assert rows[(M_WHOLE, "whole")]["sidecars"].startswith("movies (FAKEMOV0001) /sdcard/Media/X/")


def test_devices_only_says_which_phone_was_not_listed(world, tmp_path, capsys):
    world.save()
    world.devices["complete"] = False
    world.devices["devices"]["movies"].update(status="not-connected", detail="adb devices does not list it", files=[])
    res = world.run("--devices-only", "--account", "tv", out=tmp_path / "run", capsys=capsys)
    assert {o["manual_id"] for o in res["objects"]} == {T_SEASON, T_EP1, T_EP2}
    assert {o["state"] for o in res["objects"]} == {"unchecked"}
    assert "Unchecked (3) — a configured phone was not listed: movies (FAKEMOV0001) not-connected" in res["stdout"]


def test_an_unreadable_resolutions_file_stops_an_apply_before_anything_is_recorded(mixed, tmp_path, capsys):
    """A mistyped --resolutions path must not become a half-intended apply."""
    before = library_files(mixed.sandbox)
    res = mixed.run("--apply", "--resolutions", str(tmp_path / "typo.csv"), out=tmp_path / "run", capsys=capsys)
    assert (res["status"], res["applied"]) == ("error", None)
    assert library_files(mixed.sandbox) == before and not os.path.exists(mvcommon.GPHOTOS_STATE_DIR)
    assert "❌ --apply was NOT run: the --resolutions file could not be read" in res["stdout"]
    assert res["stdout"].startswith("=== MAP PHOTOS (dry-run)")


def test_a_bad_out_directory_fails_before_anything_is_recorded(mixed, tmp_path, capsys):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    before = library_files(mixed.sandbox)
    res = mixed.run("--apply", out=blocker / "run", capsys=capsys)
    assert res["status"] == "error" and "cannot create the output directory" in res["stdout"]
    assert library_files(mixed.sandbox) == before and not os.path.exists(mvcommon.GPHOTOS_STATE_DIR)


# ============================================================================ all four prefixes

def test_an_others_object_maps_and_applies_once_its_account_is_crawled(world, tmp_path):
    """The fourth prefix end to end: `oth-` objects are withheld only while the Others account has no inventory (the
    real state today); once crawled they map and apply into library_others.json like any other."""
    world.inv.enumerated["others"] = ENUMERATED
    world.save().upload_all(accounts=mvcommon.GP_ACCOUNTS).save()
    res = world.run("--apply", out=tmp_path / "run")
    rows = by_key(res)
    assert {r["category"] for r in rows.values()} == {"movies", "tv", "anime", "others"}
    assert {r["status"] for r in rows.values()} == {"mapped"}
    other = rows[(O_MATCH, "whole")]
    assert (other["account"], other["default_account"], other["proof"]) == ("others", "others", "filename")
    assert res["fresh"]["others"]["fresh"] is True and set(res["unmatched"]) == set(mvcommon.GP_ACCOUNTS)
    stored = json.loads(world.sandbox["lib_others"].read_text(encoding="utf-8"))[O_MATCH]["gphotos"]
    assert stored["home_account"] == "others" and stored["accounts"]["others"]["confirmed"] is True
    assert [r["manual_id"] for r in mvcommon.read_learned("others")] == [O_MATCH]


# ============================================================================ the CLI dispatcher branch

def _main_guard():
    """main.py's `if __name__ == "__main__":` body, compiled alone (the harness of test_prep_push_rep_enrich_cli)."""
    import ast
    with open(main.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=main.__file__)
    node = next(n for n in tree.body if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                and getattr(n.test.left, "id", None) == "__name__")
    return compile(ast.Module(body=node.body, type_ignores=[]), main.__file__, "exec")


def _dispatch(argv, status="ok"):
    """Run the dispatcher with `argv` against a recorder for cmd_map_photos -> its recorded kwargs."""
    import sys as real_sys

    class Sys:
        def __init__(self):
            self.argv = ["main.py", "map_photos", *argv]

        def __getattr__(self, name):
            return getattr(real_sys, name)

    calls = []
    namespace = dict(main.__dict__, sys=Sys(),
                     cmd_map_photos=lambda **kwargs: calls.append(kwargs) or {"status": status})
    exec(_main_guard(), namespace)
    return calls


def test_the_dispatcher_hands_the_parsed_options_to_cmd_map_photos():
    calls = _dispatch(["tv-en", "--account", "tv", "--apply", "--repair", "names", "--out", r"Z:\run"])
    assert calls == [{"scope": "tv-en", "account": "tv", "devices_only": False, "resolutions": None,
                      "repairs": ("names",), "accept_probable": False, "apply": True, "out": r"Z:\run"}]


def test_the_dispatcher_exits_1_on_a_usage_error_or_a_failed_run(capsys):
    with pytest.raises(SystemExit) as stop:
        _dispatch(["--frobnicate"])
    assert stop.value.code == 1
    out = capsys.readouterr().out
    assert "❌ map_photos: unknown option --frobnicate" in out and f"   Usage: {main.MAP_USAGE}" in out
    with pytest.raises(SystemExit) as stop:
        _dispatch(["tv-xx-none"], status="error")
    assert stop.value.code == 1
    assert _dispatch([]) == [dict(main.parse_map_photos_args([]))]           # a good run returns normally


def test_report_wording_for_an_unreadable_failed_csv_and_a_chunk_uploaded_twice():
    ctx = {"rows": [], "repairs": (), "apply": True, "applied": {
        "split_info": None, "error": None, "learned": (0, 0), "hints": (0, 0), "failed": (0, 0),
        "engine": {"status": "up-to-date", "changed": [], "confirmed": 0, "conflicts": [], "orphans": []},
        "failed_csv": {"path": "x", "kept": True, "edited_rows": None}}}
    assert main._mp_apply_lines(ctx)[-1] == ("  failed_objects.csv: kept — it no longer reads as the failed list "
                                             "(left as is)")
    twice = [{"account": "tv", "photo_id": f"AF1QipFAKEtwice{n}xxxxxxxxxx", "filename": "X [aaaaaa].chunk.001.mkv",
              "taken_ms": 1_000_000, "dedup_key": _key(b"same"), "upload_ms": n} for n in (1, 2)]
    row = {"account": "tv", "uploaded_name": "X [aaaaaa].mkv"}
    obj = {"manual_id": "tv-x", "label": "whole", "slot": ("tv-x",), "sha256": _h("x"), "kind": "whole"}
    plan = main._mp_split_plan(row, obj, twice, [], {})
    assert (plan["provable"], plan["why"]) == (False, "its chunks were uploaded more than once (the same chunk twice)")
