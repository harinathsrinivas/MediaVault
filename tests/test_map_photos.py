"""IMP-C25 Step 9 — `map_photos`: map the uploaded library objects to the Google Photos inventory (PLAN Step 9).

Pinned here, per sub-task:
  9.1 the freshness gate (no inventory / no complete enumeration / older than 24 h / older than the newest capture
      push -> stale-inventory + the re-crawl command); the CLI; the scope (all / id / prefix / alias, --account by
      routing); a dry run writes nothing but its run directory (library files hashed before and after);
      compatibility with the REAL crawler's files (a crawl of the fake Google Photos).
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
