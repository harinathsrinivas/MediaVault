"""IMP-C25 Step 3 — the Google Photos identity primitives in mvcommon (PLAN Step 3 a–g).

Pinned here, because every later IMP-C25 step builds on it:
  (a) the state constants and the autouse hermetic redirect (tests/conftest.py `_hermetic_gphotos_state`);
  (b) the `gphotos` block: construction, the P-2 pin rule, account records and the P-4 learned home account
      (drift-pinned against mainfetch.profile_for_id);
  (c) the uploaded-object inventory over all four prefixes — unsplit, split + FLAC holder, extras whole,
      extras split — alias- and season_map-safe, with slots, trusted blocks and cloud states;
  (d) remote_upload_name, drift-pinned against cmd_prep, cmd_push (the names on the device),
      push_one_extra and gpcapture.snapshot_push_objects;
  (e) read_capture over capture v1 files written by the REAL gpcapture functions, and v2-shaped files;
  (f) the learned store (the P-10 line), its reducer and pure apply step, the failed list (P-18) with its CSV
      round trip, and the offline pending list (P-11).
SYNTHETIC data only: every photo id contains FAKE; the only real identity values are the published S02E03
example (RESEARCH F20 / F21). Nothing here touches C:\\Media, library_*.json, ~/.mediavault or the real
inventory dir (the autouse redirect + the `sandbox` fixture guarantee it).
"""
import ast
import copy
import csv
import doctest
import hashlib
import io
import json
import os
import random
import subprocess
import sys
from datetime import datetime, timezone

import pytest

import gpcapture
import gpweb
import main
import mainfetch
import mvcommon
from test_entry_schema_guard import _extras_block  # THE canonical extras shape (IMP-D19 guard)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Read at IMPORT (collection) time — before the autouse fixture redirects them for each test.
REAL_GPHOTOS_STATE_DIR = mvcommon.GPHOTOS_STATE_DIR
REAL_GP_INVENTORY_DIR = mvcommon.GP_INVENTORY_DIR

S02E03_SHA1 = "85df66012c86f692618ac3d7152dd9afb32aaf6f"  # RESEARCH F21 — published example
S02E03_KEY = "hd9mASyG9pJhisPXFS3Zr7Mqr28"
S02E03_TAKEN_MS = 1644061786000                         # RESEARCH F20: 2022-02-05T11:49:46Z

PID = [f"AF1QipFAKEprim{i:02d}AAAA0123456789FAKE" for i in range(12)]


def _h(tag):
    """A synthetic SHA-256 hex."""
    return hashlib.sha256(tag.encode()).hexdigest()


def _key(tag):
    """A synthetic dedupKey: the real key of synthetic bytes."""
    return gpweb.dedup_key(hashlib.sha1(tag.encode()).hexdigest())


def T(minute, day=20):
    """A '…Z' timestamp on 2026-09-<day> at 10:<minute>."""
    return f"2026-09-{day:02d}T10:{minute:02d}:00Z"


# ================================================================== synthetic library (all four prefixes)

MOVIE = "mov-en-2020-wholemovie"        # unsplit movie leaf + extras (whole, split, not-yet-pushed)
SPLITMOVIE = "mov-en-2021-splitmovie"   # split leaf: 3 chunks + a FLAC holder
SEASON = "tv-en-2015-show-s02"          # season_map with extras
EP_WHOLE = "tv-en-2015-show-s02e02"
EP_SPLIT = "tv-en-2015-show-s02e03"     # combined-episode file, split
EP_ALIAS = "tv-en-2015-show-s02e04"     # multi_ep_alias -> EP_SPLIT
BARE_SEASON = "tv-en-2016-bare-s01"     # season_map WITHOUT extras
LOCAL_EP = "tv-en-2016-bare-s01e01"     # never pushed
ANIME = "ani-ja-2006-deathnote07"
OTHER = "oth-en-2024-final-e01"         # split


def _entry(mid, folder, filename, status="archived", uploaded=True, chunks=0, holder=False):
    sid = mvcommon.generate_short_id(mid)
    stem, ext = os.path.splitext(filename)
    entry = {"short_id": sid, "filename": filename, "folder_path": str(folder), "status": status,
             "uploaded": uploaded, "search_term": f"{stem} [{sid}]{ext}", "hash": _h(mid)}
    if chunks:
        entry["split_info"] = {
            "is_split": True, "method": "SIZE_GB", "val": "8", "total_chunks": chunks,
            "chunks": [{"filename": f"{stem} [{sid}].chunk.{i:03d}.mkv", "hash": _h(f"{mid}#c{i}")}
                       for i in range(1, chunks + 1)]}
        if holder:
            entry["split_info"]["carried_out_tracks"] = [
                {"track_id": 3, "codec": "A_FLAC", "language": "eng",
                 "holder_filename": f"{stem} [{sid}].holder.mkv", "holder_hash": _h(f"{mid}#holder")}]
    return entry


def _extra(title_id, group_rel, sub_rel, status="onboarded", uploaded=True, chunks=0):
    filename = sub_rel.split("/")[-1]
    sid = mvcommon.generate_short_id(f"{title_id}::{group_rel}/{sub_rel}")
    stem, ext = os.path.splitext(filename)
    item = {"filename": filename, "sub_rel": sub_rel, "short_id": sid, "hash": _h(f"{title_id}::{sub_rel}"),
            "status": status, "uploaded": uploaded, "search_term": f"{stem} [{sid}]{ext}"}
    if chunks:
        item["split_info"] = {
            "is_split": True, "method": "COUNT", "val": str(chunks), "total_chunks": chunks,
            "chunks": [{"filename": f"{stem} [{sid}].chunk.{i:03d}.mkv", "hash": _h(f"{sid}#c{i}")}
                       for i in range(1, chunks + 1)]}
    return item


@pytest.fixture()
def lib(tmp_path):
    """An in-memory library over all four prefixes; every folder lies under tmp_path (none needs to exist)."""
    root = tmp_path / "Media"
    movie_dir = root / "Movies" / "Whole Movie (2020)"
    season_dir = root / "Series" / "Show" / "Season 02"
    return {
        MOVIE: {**_entry(MOVIE, movie_dir, "Whole.Movie.2020.mkv"), "extras": {"groups": {
            "Specials": {"added_date": "2026-09-01", "items": [
                _extra(MOVIE, "Specials", "BTS.mkv"),
                _extra(MOVIE, "Specials", "Bonus/Making Of.mkv", status="archived", chunks=2)]},
            "Extra": {"added_date": "2026-09-01", "items": [
                _extra(MOVIE, "Extra", "Trailer.mkv", status="local_ready", uploaded=False)]}}}},
        SPLITMOVIE: _entry(SPLITMOVIE, root / "Movies" / "Split Movie (2021)", "Split.Movie.2021.mkv",
                           chunks=3, holder=True),
        SEASON: {"type": "season_map", "folder_path": str(season_dir), "total_episodes": 3,
                 "children": [EP_WHOLE, EP_SPLIT, EP_ALIAS],
                 "extras": {"groups": {"Specials": {"added_date": "2026-09-01", "items": [
                     _extra(SEASON, "Specials", "Recap.mkv")]}}}},
        EP_WHOLE: {**_entry(EP_WHOLE, season_dir, "Show.S02E02.mkv"), "parent_id": SEASON},
        EP_SPLIT: {**_entry(EP_SPLIT, season_dir, "Show.S02E03E04.mkv", status="onboarded", chunks=2),
                   "parent_id": SEASON},
        EP_ALIAS: {"type": "multi_ep_alias", "alias_of": EP_SPLIT, "parent_id": SEASON},
        BARE_SEASON: {"type": "season_map", "folder_path": str(root / "Series" / "Bare" / "Season 01"),
                      "total_episodes": 1, "children": [LOCAL_EP]},
        LOCAL_EP: {**_entry(LOCAL_EP, root / "Series" / "Bare" / "Season 01", "Bare.S01E01.mkv",
                            status="local_ready", uploaded=False), "parent_id": BARE_SEASON},
        ANIME: _entry(ANIME, root / "Anime" / "Death Note", "Death Note - 07 [1080p].mkv"),
        OTHER: _entry(OTHER, root / "Sports" / "Final 2024", "Final.Match.2024.mkv", chunks=2),
    }


def _labels(objects):
    return [(o["manual_id"], o["kind"], o["label"]) for o in objects]


# ================================================================== (a) constants + hermetic redirect

def test_state_constants_have_their_documented_real_values():
    assert REAL_GPHOTOS_STATE_DIR == os.path.join(mvcommon.MV_STATE_DIR, "gphotos")
    assert REAL_GP_INVENTORY_DIR == r"D:\MediaVault_gp_inventory"


def test_inventory_dir_equals_the_crawler_default_output():
    with open(os.path.join(REPO, "tools", "gp_inventory.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    default_out = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "DEFAULT_OUT" for t in node.targets))
    assert default_out == REAL_GP_INVENTORY_DIR


def test_autouse_redirect_points_every_state_path_into_tmp_path(tmp_path):
    for live, real in ((mvcommon.GPHOTOS_STATE_DIR, REAL_GPHOTOS_STATE_DIR),
                       (mvcommon.GP_INVENTORY_DIR, REAL_GP_INVENTORY_DIR)):
        assert live != real
        assert tmp_path.resolve() in type(tmp_path)(live).resolve().parents
    for path in (mvcommon.learned_path("tv"), mvcommon.failed_path(), mvcommon.failed_csv_path()):
        assert path.startswith(mvcommon.GPHOTOS_STATE_DIR)
    written = mvcommon.append_learned("tv", {"manual_id": EP_WHOLE, "sha256": _h("x"), "source": "check",
                                             "approach": {"tried": "s2_exact_name", "outcome": "not-found"}})
    assert written["account"] == "tv"
    assert os.path.isfile(os.path.join(str(tmp_path), "mv_state", "gphotos", "learned_tv.jsonl"))


def test_mvcommon_imports_no_main_mainfetch_gpcapture_or_selenium():
    code = ("import sys, mvcommon; print(sorted(m for m in ('main', 'mainfetch', 'gpcapture', 'selenium', "
            "'gpweb') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "['gpweb']"
    with open(os.path.join(REPO, "mvcommon.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert not imported & {"main", "mainfetch", "gpcapture", "gprpc", "webui"}


def test_vocabularies_match_mainfetch_gpcapture_and_the_plan():
    assert set(mvcommon.GP_ACCOUNTS) == set(mainfetch.CHROME_PROFILES)
    assert set(mvcommon.GP_CATEGORY_PREFIXES) == set(mainfetch.ID_PREFIX_PROFILE)
    assert mvcommon._CAPTURE_SUFFIX == gpcapture.CAPTURE_SUFFIX
    assert mvcommon._PHOTO_URL == mainfetch.PHOTOS_URL + "/photo/"
    # P-10's five stages are all accepted, plus P-8's S0b / S3u; every stage maps onto a P-1 approach
    assert {"s0_photo_id", "s1_taken_second", "s2_exact_name", "s3_title_sid", "s4_legacy"} <= set(mvcommon.GP_STAGES)
    assert set(mvcommon.GP_STAGES.values()) == set(mvcommon.GP_APPROACHES)
    assert mvcommon.GP_FAILED_CSV_COLUMNS[:17] == (
        "account", "category", "manual_id", "object", "uploaded_name", "status", "candidates",
        "suggested_photo_url", "expected_taken_local", "decision", "photo_url", "date", "time", "gmt",
        "new_search_term", "set_search_cmd", "note")


@pytest.mark.parametrize("manual_id,category", [
    (MOVIE, "movies"), (EP_WHOLE, "tv"), (ANIME, "anime"), (OTHER, "others"), ("zzz-unknown", None), (None, None)])
def test_category_for_id(manual_id, category):
    assert mvcommon.category_for_id(manual_id) == category


# ================================================================== (b) the block

FULL_ACCOUNT = {
    "photo_id": PID[0], "taken_ms": S02E03_TAKEN_MS, "tz_offset_ms": 28_800_000, "upload_ms": 1_790_104_139_274,
    "dedup_key": S02E03_KEY, "gp_filename": "Sample.S02E03 [a1b2c3].mkv", "width": 1920, "height": 1080,
    "verified_at": T(1), "verified_by": "dedupkey", "source": "check",
    "approach": {"best": "photo_id", "ok": ["photo_id", "hash", "exact_name"], "failed": ["title_sid"],
                 "at": T(1), "via": "rpc"},
    "alternates": [PID[1]], "original_quality": True, "partial_upload": False, "confirmed": True}


def _full_block(sha256):
    return mvcommon.new_gphotos_block(
        sha256, search_override={"query": "sample s02e03", "at": T(2)}, home_account="tv",
        accounts={"tv": FULL_ACCOUNT}, pushed={"device_tz_offset_min": 480, "device_tz": "Asia/Singapore",
                                              "device_id": "FAKESERIAL01", "at": T(0)},
        uploaded_name="Sample.S02E03 [a1b2c3].mkv", date_utc="2022-02-05T11:49:46Z", duration_s=1742.5,
        height=1080, width=1920, size_bytes=4_294_967_296, sha1=S02E03_SHA1)


def test_new_block_carries_every_p1_key_in_p1_order_and_round_trips():
    block = _full_block(_h("full"))
    assert list(block) == ["v", "sha256", "sha1", "dedup_key", "size_bytes", "width", "height", "duration_s",
                           "date_utc", "uploaded_name", "pushed", "accounts", "home_account", "search_override"]
    assert block["v"] == 1 and block["dedup_key"] == S02E03_KEY  # derived from the published SHA-1
    assert list(block["pushed"]) == ["at", "device_id", "device_tz", "device_tz_offset_min"]
    assert list(block["accounts"]["tv"]) == list(FULL_ACCOUNT)
    assert json.loads(json.dumps(block)) == block
    assert mvcommon.block_trusted(block, _h("full"))


def test_new_block_drops_unknown_facts_and_derives_the_other_content_key():
    block = mvcommon.new_gphotos_block(_h("a"), size_bytes=None, uploaded_name="x [abc].mkv",
                                       pushed={"at": None, "device_id": None}, accounts={},
                                       dedup_key=S02E03_KEY)
    assert block == {"v": 1, "sha256": _h("a"), "sha1": S02E03_SHA1, "dedup_key": S02E03_KEY,
                     "uploaded_name": "x [abc].mkv"}
    std = gpweb.sha1_hex_to_std_b64(S02E03_SHA1)  # the swbisb spelling is canonicalized
    assert mvcommon.new_gphotos_block(_h("a"), dedup_key=std)["dedup_key"] == S02E03_KEY
    assert mvcommon.new_gphotos_block(_h("a"), sha1=S02E03_SHA1.upper())["sha1"] == S02E03_SHA1


def test_new_block_json_is_independent_of_argument_order():
    a = mvcommon.new_gphotos_block(_h("o"), width=1, height=2, uploaded_name="n [abc].mkv")
    b = mvcommon.new_gphotos_block(_h("o"), uploaded_name="n [abc].mkv", height=2, width=1)
    assert json.dumps(a) == json.dumps(b)


@pytest.mark.parametrize("sha256,facts", [
    (None, {}), ("abc", {}), ("a" * 63, {}), ("g" * 64, {}),
    (_h("x"), {"dedupkey": S02E03_KEY}),                             # typo'd key
    (_h("x"), {"v": 2}),                                               # the version is not a fact
    (_h("x"), {"sha1": "xyz"}), (_h("x"), {"dedup_key": "short"}),
    (_h("x"), {"sha1": S02E03_SHA1, "dedup_key": _key("other")}),     # two different contents
    (_h("x"), {"size_bytes": -1}), (_h("x"), {"width": 1.5}), (_h("x"), {"duration_s": True}),
    (_h("x"), {"date_utc": "2022-02-05"}), (_h("x"), {"uploaded_name": "  "}),
    (_h("x"), {"pushed": "FA75"}), (_h("x"), {"accounts": {"films": {"photo_id": PID[0]}}}),
    (_h("x"), {"accounts": {"tv": "PID"}}), (_h("x"), {"home_account": "films"}),
])
def test_new_block_refuses_invalid_input(sha256, facts):
    with pytest.raises(ValueError):
        mvcommon.new_gphotos_block(sha256, **facts)


def test_block_trusted_is_the_p2_pin_rule():
    sha = _h("pin")
    block = mvcommon.new_gphotos_block(sha)
    assert mvcommon.block_trusted(block, sha)
    assert not mvcommon.block_trusted(block, _h("other bytes"))        # re-split / re-master
    assert not mvcommon.block_trusted(block, None)
    assert not mvcommon.block_trusted(block, "smokehash1")              # not a SHA-256
    assert not mvcommon.block_trusted(dict(block, v=2), sha)            # a version this code cannot read
    assert not mvcommon.block_trusted(dict(block, v=True), sha)         # bool is not the version
    assert not mvcommon.block_trusted({"sha256": sha}, sha)             # no version
    assert not mvcommon.block_trusted(None, sha)
    assert not mvcommon.block_trusted([block], sha)


def test_account_record_is_a_copy():
    block = _full_block(_h("acct"))
    rec = mvcommon.account_record(block, "tv")
    assert rec == FULL_ACCOUNT
    rec["photo_id"] = "changed"
    assert block["accounts"]["tv"]["photo_id"] == PID[0]
    assert mvcommon.account_record(block, "movies") is None
    assert mvcommon.account_record(None, "tv") is None
    assert mvcommon.account_record({"accounts": ["tv"]}, "tv") is None


@pytest.mark.parametrize("manual_id", [MOVIE, EP_WHOLE, ANIME, OTHER, "zzz-unknown-prefix", "movie-no-dash"])
def test_home_account_default_is_exactly_profile_for_id(manual_id, monkeypatch):
    """Drift pin (PLAN Step 3b): with no learned records the home account IS mainfetch.profile_for_id."""
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {})  # no mvconfig fetch_account_overrides can leak in
    assert mvcommon.home_account(None, manual_id, mainfetch.ID_PREFIX_PROFILE, mainfetch.DEFAULT_PROFILE) \
        == mainfetch.profile_for_id(manual_id)


def _home(accounts=None, stored=None, manual_id=EP_WHOLE):
    block = {"v": 1, "sha256": _h("home")}
    if accounts is not None:
        block["accounts"] = accounts
    if stored is not None:
        block["home_account"] = stored
    return mvcommon.home_account(block, manual_id, mainfetch.ID_PREFIX_PROFILE, mainfetch.DEFAULT_PROFILE)


def test_home_account_follows_the_verified_copies_p4():
    verified = lambda proof, at=T(1): {"photo_id": PID[0], "verified_by": proof, "verified_at": at}
    assert _home({}) == "tv"
    # an X-Files-like tv id whose only verified copy is in MOVIES -> movies (IMP-C26)
    assert _home({"movies": verified("filename")}) == "movies"
    # the prefix-default account wins whenever it holds a verified copy (Kuroko copies in TV — P-4)
    assert _home({"movies": verified("dedupkey"), "tv": verified("filename")}) == "tv"
    # several non-default copies: strongest proof, then newest, then name
    assert _home({"movies": verified("filename"), "anime": verified("sha256")}) == "anime"
    assert _home({"movies": verified("filename", T(5)), "anime": verified("filename", T(1))}) == "movies"
    assert _home({"movies": verified("filename"), "anime": verified("filename")}) == "anime"
    # a manual match is a better guess than the prefix, but any verified copy beats it
    assert _home({"movies": verified("manual")}) == "movies"
    assert _home({"movies": verified("manual"), "anime": verified("filename")}) == "anime"
    assert _home({"tv": verified("manual"), "movies": verified("manual")}) == "tv"
    # records without a proof (approach evidence only) do not move the object
    assert _home({"movies": {"approach": {"failed": ["exact_name"]}}}) == "tv"


def test_home_account_stored_value_and_invalid_input():
    assert _home(stored="movies") == "movies"                           # the engine's stored decision
    assert _home(stored="films") == "tv"                                # not an account -> ignored
    assert _home(stored=["movies"]) == "tv"
    assert _home({"films": {"verified_by": "dedupkey"}}) == "tv"        # unknown account key ignored
    assert _home({"movies": "not a record"}) == "tv"
    assert _home({"tv": {"verified_by": "filename"}}, stored="movies") == "tv"  # records outrank storage
    assert mvcommon.home_account("junk", ANIME, mainfetch.ID_PREFIX_PROFILE, "movies") == "anime"


# ================================================================== (c) + (d) objects and names

def test_iter_uploaded_objects_whole_library_order_kinds_and_alias_safety(lib):
    objects = list(mvcommon.iter_uploaded_objects(lib))
    specials = "extra Specials/"
    assert _labels(objects) == [
        (ANIME, "whole", "whole"),
        (MOVIE, "whole", "whole"),
        (MOVIE, "extra_whole", specials + "BTS.mkv"),
        (MOVIE, "extra_chunk", specials + "Bonus/Making Of.mkv chunk 1/2"),
        (MOVIE, "extra_chunk", specials + "Bonus/Making Of.mkv chunk 2/2"),
        (SPLITMOVIE, "chunk", "chunk 1/3"), (SPLITMOVIE, "chunk", "chunk 2/3"), (SPLITMOVIE, "chunk", "chunk 3/3"),
        (SPLITMOVIE, "holder", "holder"),
        (OTHER, "chunk", "chunk 1/2"), (OTHER, "chunk", "chunk 2/2"),
        (SEASON, "extra_whole", specials + "Recap.mkv"),
        (EP_WHOLE, "whole", "whole"),
        (EP_SPLIT, "chunk", "chunk 1/2"), (EP_SPLIT, "chunk", "chunk 2/2"),
    ]
    # never the alias, never the extras-less season, never a not-yet-pushed object (Trailer, LOCAL_EP)
    assert {o["cloud_state"] for o in objects} == {"uploaded"}
    assert [o["category"] for o in objects if o["kind"] in ("whole", "holder")] == [
        "anime", "movies", "movies", "tv"]
    assert {o["category"] for o in objects if o["manual_id"] == OTHER} == {"others"}


def test_split_leaf_uploads_chunks_and_holders_never_the_whole_file_p3(lib):
    objects = list(mvcommon.iter_entry_objects(SPLITMOVIE, lib[SPLITMOVIE]))
    entry = lib[SPLITMOVIE]
    assert [o["kind"] for o in objects] == ["chunk", "chunk", "chunk", "holder"]
    assert [o["sha256"] for o in objects] == [c["hash"] for c in entry["split_info"]["chunks"]] + [
        entry["split_info"]["carried_out_tracks"][0]["holder_hash"]]
    assert entry["hash"] not in {o["sha256"] for o in objects}
    assert [o["index"] for o in objects] == [1, 2, 3, 1]
    assert [o["uploaded_name"] for o in objects] == [c["filename"] for c in entry["split_info"]["chunks"]] + [
        entry["split_info"]["carried_out_tracks"][0]["holder_filename"]]
    assert objects[3]["filename"] == entry["split_info"]["carried_out_tracks"][0]["holder_filename"]


def test_aliases_and_extras_less_season_maps_yield_nothing(lib, sandbox_alias):
    assert list(mvcommon.iter_entry_objects(EP_ALIAS, lib[EP_ALIAS])) == []
    assert list(mvcommon.iter_entry_objects(BARE_SEASON, lib[BARE_SEASON])) == []
    season = list(mvcommon.iter_entry_objects(SEASON, lib[SEASON]))
    assert [o["kind"] for o in season] == ["extra_whole"]
    # the real alias chain the sandbox_alias fixture seeds (season_map + leaf + multi_ep_alias)
    library = mvcommon.load_library()
    alias_entry = library[sandbox_alias["alias_id"]]
    assert list(mvcommon.iter_entry_objects(sandbox_alias["alias_id"], alias_entry)) == []
    assert list(mvcommon.iter_uploaded_objects(library)) == []          # the primary is only local_ready
    library[sandbox_alias["primary_id"]]["status"] = "archived"
    objects = list(mvcommon.iter_uploaded_objects(library))
    assert _labels(objects) == [(sandbox_alias["primary_id"], "whole", "whole")]


def test_explicit_ids_resolve_aliases_once_and_skip_unknown_ids(lib):
    via_alias = list(mvcommon.iter_uploaded_objects(lib, [EP_ALIAS]))
    assert _labels(via_alias) == [(EP_SPLIT, "chunk", "chunk 1/2"), (EP_SPLIT, "chunk", "chunk 2/2")]
    assert _labels(mvcommon.iter_uploaded_objects(lib, [EP_ALIAS, EP_SPLIT])) == _labels(via_alias)
    assert _labels(mvcommon.iter_uploaded_objects(lib, EP_SPLIT)) == _labels(via_alias)   # one id as a str
    assert list(mvcommon.iter_uploaded_objects(lib, ["nope", 7, None])) == []
    assert [o["kind"] for o in mvcommon.iter_uploaded_objects(lib, [SEASON])] == ["extra_whole"]
    dangling = dict(lib, **{"tv-en-2015-show-s02e09": {"type": "multi_ep_alias", "alias_of": "gone"}})
    assert list(mvcommon.iter_uploaded_objects(dangling, ["tv-en-2015-show-s02e09"])) == []
    assert list(mvcommon.iter_uploaded_objects(None)) == []


def test_every_object_slot_resolves_to_its_owning_node(lib):
    objects = list(mvcommon.iter_entry_objects(MOVIE, lib[MOVIE])) + list(
        mvcommon.iter_entry_objects(SPLITMOVIE, lib[SPLITMOVIE]))
    for obj in objects:
        owner = mvcommon.slot_owner(lib, obj["slot"])
        hash_key = "holder_hash" if obj["kind"] == "holder" else "hash"
        assert owner is not None and owner[hash_key] == obj["sha256"], obj["label"]
        # the JSON form of a slot (a list) works the same
        assert mvcommon.slot_owner(lib, json.loads(json.dumps(obj["slot"]))) is owner
    assert objects[0]["slot"] == (MOVIE,)
    assert mvcommon.slot_owner(lib, (SPLITMOVIE, "split_info", "chunks", 9)) is None
    assert mvcommon.slot_owner(lib, ()) is None
    assert mvcommon.slot_owner(lib, "not a slot") is None


def test_local_objects_are_listed_per_entry_but_not_as_uploaded(lib):
    movie = list(mvcommon.iter_entry_objects(MOVIE, lib[MOVIE]))
    trailer = [o for o in movie if o["label"] == "extra Extra/Trailer.mkv"]
    assert [o["cloud_state"] for o in trailer] == ["local"]
    assert [o["kind"] for o in movie] == ["whole", "extra_whole", "extra_whole", "extra_chunk", "extra_chunk"]
    assert [o["cloud_state"] for o in mvcommon.iter_entry_objects(LOCAL_EP, lib[LOCAL_EP])] == ["local"]


def test_extras_objects_carry_their_name_parts_and_push_names(lib):
    item = lib[MOVIE]["extras"]["groups"]["Specials"]["items"][0]
    split_item = lib[MOVIE]["extras"]["groups"]["Specials"]["items"][1]
    bts, c1, c2 = [o for o in mvcommon.iter_entry_objects(MOVIE, lib[MOVIE]) if o["group_rel"] == "Specials"]
    assert (bts["group_rel"], bts["sub_rel"], bts["short_id"]) == ("Specials", "BTS.mkv", item["short_id"])
    assert bts["uploaded_name"] == f"BTS [{item['short_id']}].mkv" == item["search_term"]
    assert [c1["uploaded_name"], c2["uploaded_name"]] == [c["filename"] for c in split_item["split_info"]["chunks"]]
    assert c2["slot"] == (MOVIE, "extras", "groups", "Specials", "items", 1, "split_info", "chunks", 1)
    assert c1["sub_rel"] == "Bonus/Making Of.mkv" and c1["manual_id"] == MOVIE


def test_canonical_extras_block_on_both_title_shapes(tmp_path):
    """The schema guard's canonical `extras` block (both title shapes): every item is an object; the split
    item's chunks replace it; the not-yet-pushed item is local."""
    movie_id, season_id = "mov-en-2024-guardmovie", "tv-en-2009-bsg-s04"
    movie = {"folder_path": str(tmp_path / "m"), "filename": "guard_movie.mkv", "status": "local_ready",
             "uploaded": False, "extras": _extras_block(movie_id)}
    season = {"type": "season_map", "folder_path": str(tmp_path / "s"), "children": [],
              "extras": _extras_block(season_id)}
    for mid, entry, leaf in ((movie_id, movie, 1), (season_id, season, 0)):
        objects = list(mvcommon.iter_entry_objects(mid, entry))
        assert [o["kind"] for o in objects] == ["whole"] * leaf + [
            "extra_whole", "extra_whole", "extra_whole", "extra_chunk", "extra_chunk"]
        extras = objects[leaf:]
        assert [o["cloud_state"] for o in extras] == ["uploaded", "local", "uploaded", "uploaded", "uploaded"]
        assert extras[3]["label"] == "extra Specials/Bonus/Making Of.mkv chunk 1/2"
        assert extras[3]["uploaded_name"].endswith(".chunk.001.mkv")


@pytest.mark.parametrize("filename,short_id,kind,expected", [
    ("Show.S01E02.1080p.mkv", "a1b2c3", None, "Show.S01E02.1080p [a1b2c3].mkv"),
    ("Movie.2019 [d4e5f6].chunk.002.mkv", "d4e5f6", None, "Movie.2019 [d4e5f6].chunk.002.mkv"),
    ("Movie.2019 [d4e5f6].holder.mkv", "d4e5f6", None, "Movie.2019 [d4e5f6].holder.mkv"),
    ("Movie.2019 [d4e5f6].CHUNK.002.MKV", "d4e5f6", None, "Movie.2019 [d4e5f6].CHUNK.002.MKV"),
    ("Clip.mkv.mkv", "0a0b0c", None, "Clip.mkv [0a0b0c].mkv"),        # the real double extension
    ("Film.mp4", "0a0b0c", None, "Film [0a0b0c].mp4"),
    ("NoExtension", "0a0b0c", None, "NoExtension [0a0b0c]"),
    ("Show.S01E02.mkv", None, None, None),                             # a whole file needs its short_id
    ("Show.S01E02.mkv", "", "whole", None),
    (None, "a1b2c3", None, None), ("", "a1b2c3", None, None),
    # the kind decides when known: a WHOLE file whose own name looks like a chunk is still tagged
    ("Odd.chunk.001.mkv", "a1b2c3", "whole", "Odd.chunk.001 [a1b2c3].mkv"),
    ("Odd.chunk.001.mkv", "a1b2c3", "extra_whole", "Odd.chunk.001 [a1b2c3].mkv"),
    ("Plain.mkv", "a1b2c3", "chunk", "Plain.mkv"),
    ("Plain.mkv", "a1b2c3", "holder", "Plain.mkv"),
    ("Plain.mkv", "a1b2c3", "extra_chunk", "Plain.mkv"),
])
def test_remote_upload_name_table(filename, short_id, kind, expected):
    assert mvcommon.remote_upload_name(filename, short_id, kind) == expected


def test_remote_upload_name_rejects_an_unknown_kind_and_its_doctests_pass():
    with pytest.raises(ValueError):
        mvcommon.remote_upload_name("x.mkv", "a1b2c3", "episode")
    runner = doctest.DocTestRunner()
    for test in doctest.DocTestFinder().find(mvcommon.remote_upload_name, "remote_upload_name",
                                             globs={"remote_upload_name": mvcommon.remote_upload_name}):
        runner.run(test)
    assert runner.failures == 0 and runner.tries == 2


@pytest.mark.parametrize("filename,short_id", [
    ("Show.S01E02.1080p.mkv", "a1b2c3"), ("Movie.2019 [d4e5f6].chunk.002.mkv", "d4e5f6"),
    ("Movie.2019 [d4e5f6].holder.mkv", "d4e5f6"), ("Clip.mkv.mkv", "0a0b0c"), ("Show.mkv", None)])
def test_remote_upload_name_equals_the_s2_query_builder(filename, short_id):
    assert gpweb.exact_name_query({"filename": filename, "short_id": short_id}) \
        == mvcommon.remote_upload_name(filename, short_id)


@pytest.mark.parametrize("manual_id", [
    "mov-en-2020-pinmovie", "tv-en-2015-pinshow-s01e01", "ani-ja-2006-pinanime01", "oth-en-2024-pinmatch-e01"])
def test_drift_pin_cmd_prep_default_search_term(sandbox, make_video, stub_tech_specs, manual_id):
    path, _ = make_video(sandbox["media_dir"] / "Pin.Object.2020.mkv")
    assert main.cmd_prep(manual_id, path) is True
    entry = mvcommon.load_library()[manual_id]
    assert mvcommon.remote_upload_name(entry["filename"], entry["short_id"]) == entry["search_term"]


def _device_names(device_dir):
    """Index the fake device by name — '[short_id]' would be a glob class (testing-strategy §8.1)."""
    return {p.name for p in device_dir.rglob("*.mkv")}


def test_drift_pin_cmd_push_whole_file_and_the_capture_it_leaves(sandbox, make_video, stub_tech_specs,
                                                                 mock_device):
    manual_id = "mov-en-2020-pushpin"
    path, sha256 = make_video(sandbox["media_dir"] / "Push.Pin.2020.mkv")
    assert main.cmd_prep(manual_id, path) is True
    assert main.cmd_push(manual_id) is True
    entry = mvcommon.load_library()[manual_id]
    name = mvcommon.remote_upload_name(entry["filename"], entry["short_id"])
    assert name in _device_names(mock_device)
    (obj,) = mvcommon.iter_entry_objects(manual_id, entry)
    assert (obj["kind"], obj["uploaded_name"], obj["sha256"]) == ("whole", name, sha256)
    with open(path, "rb") as fh:
        assert obj["capture"]["sha1"] == hashlib.sha1(fh.read()).hexdigest()
    assert obj["capture"]["pushed"]["at"].endswith("Z")
    assert obj["cloud_state"] == "unverified"      # pushed and captured, not confirmed (P-11)


def _seed_parts(folder, names, payload):
    """Pre-seed a _parts/ dir so cmd_push / push_one_extra take their RESUME branch (no mkvmerge)."""
    folder.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for i, name in enumerate(names):
        data = payload + f"#{i}".encode()
        (folder / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


def test_drift_pin_cmd_push_chunk_and_holder_names(sandbox, mock_device):
    manual_id = "mov-en-2021-splitpin"
    sid = mvcommon.generate_short_id(manual_id)
    media = sandbox["media_dir"]
    (media / "Split.Pin.2021.mkv").write_bytes(b"MASTER-BYTES" * 1000)
    chunk_names = [f"Split.Pin.2021 [{sid}].chunk.{i:03d}.mkv" for i in (1, 2)]
    holder_name = f"Split.Pin.2021 [{sid}].holder.mkv"
    hashes = _seed_parts(media / main.SPLIT_DIR_NAME, chunk_names + [holder_name], b"CHUNK")
    mvcommon.save_library({manual_id: {
        "short_id": sid, "filename": "Split.Pin.2021.mkv", "folder_path": str(media), "status": "local_ready",
        "uploaded": False, "search_term": f"Split.Pin.2021 [{sid}].mkv", "hash": _h(manual_id),
        "split_info": {"is_split": True, "method": "SIZE_GB", "val": "8", "total_chunks": 2,
                       "chunks": [{"filename": n, "hash": hashes[n]} for n in chunk_names],
                       "carried_out_tracks": [{"holder_filename": holder_name, "holder_hash": hashes[holder_name],
                                               "track_id": 3, "codec": "A_FLAC", "language": "eng"}]}}})
    assert main.cmd_push(manual_id) is True
    on_device = _device_names(mock_device)
    for name in chunk_names + [holder_name]:
        assert mvcommon.remote_upload_name(name, sid) == name and name in on_device
    entry = mvcommon.load_library()[manual_id]
    objects = list(mvcommon.iter_entry_objects(manual_id, entry))
    assert [(o["kind"], o["uploaded_name"]) for o in objects] == [
        ("chunk", chunk_names[0]), ("chunk", chunk_names[1]), ("holder", holder_name)]
    assert [o["sha256"] for o in objects] == [hashes[n] for n in chunk_names + [holder_name]]
    assert {o["cloud_state"] for o in objects} == {"unverified"}
    assert [o["capture"]["role"] for o in objects] == ["chunk", "chunk", "holder"]


def test_drift_pin_push_one_extra_whole_and_split(sandbox_extras, mock_device):
    title_id = sandbox_extras["title_id"]
    bts, trailer = sandbox_extras["items"]
    chunk_names = [f"Trailer [{trailer['short_id']}].chunk.{i:03d}.mkv" for i in (1, 2)]
    hashes = _seed_parts(sandbox_extras["extras_dir"] / main.SPLIT_DIR_NAME / trailer["short_id"],
                         chunk_names, b"EXTRA-CHUNK")
    library = mvcommon.load_library()
    items = library[title_id]["extras"]["groups"]["Specials"]["items"]
    items[1]["split_info"] = {"is_split": True, "method": "COUNT", "val": "2", "total_chunks": 2,
                              "chunks": [{"filename": n, "hash": hashes[n]} for n in chunk_names]}
    assert main.push_title_extras(library, title_id, ("NONE", None)) is True
    on_device = _device_names(mock_device)
    whole = mvcommon.remote_upload_name(bts["filename"], bts["short_id"], "extra_whole")
    assert whole == f"BTS [{bts['short_id']}].mkv" and whole in on_device
    assert all(n in on_device for n in chunk_names)
    entry = mvcommon.load_library()[title_id]
    extras = [o for o in mvcommon.iter_entry_objects(title_id, entry) if o["kind"].startswith("extra")]
    # IMP-C25 Step 5: push_one_extra now leaves the item's capture -> pushed + captured = unverified (P-11)
    assert [(o["kind"], o["uploaded_name"], o["cloud_state"]) for o in extras] == [
        ("extra_whole", whole, "unverified"), ("extra_chunk", chunk_names[0], "unverified"),
        ("extra_chunk", chunk_names[1], "unverified")]


def test_drift_pin_gpcapture_snapshot_names(tmp_path):
    parts = tmp_path / main.SPLIT_DIR_NAME
    parts.mkdir()
    files = [tmp_path / "Movie.2020.mkv", parts / "Movie.2020 [abc123].chunk.001.mkv",
             parts / "Movie.2020 [abc123].holder.mkv"]
    for f in files:
        f.write_bytes(b"x")
    objects = gpcapture.snapshot_push_objects(
        [str(f) for f in files], str(parts), "abc123",
        {"Movie.2020 [abc123].chunk.001.mkv": _h("c1")}, _h("whole"),
        [{"holder_filename": "Movie.2020 [abc123].holder.mkv", "holder_hash": _h("h")}])
    assert [o["role"] for o in objects] == ["whole", "chunk", "holder"]
    for o in objects:
        assert o["uploaded_name"] == mvcommon.remote_upload_name(o["local_name"], "abc123", o["role"]) \
            == mvcommon.remote_upload_name(o["local_name"], "abc123")


# ================================================================== trusted blocks drive names + cloud state

def test_uploaded_name_precedence_trusted_block_then_capture_then_rule(lib, tmp_path):
    entry = copy.deepcopy(lib[EP_WHOLE])
    folder = tmp_path / "ep"
    folder.mkdir()
    entry["folder_path"] = str(folder)
    rule = mvcommon.remote_upload_name(entry["filename"], entry["short_id"])
    (obj,) = mvcommon.iter_entry_objects(EP_WHOLE, entry)
    assert obj["uploaded_name"] == rule and obj["block"] is None and obj["capture"] is None
    capture = {"capture_version": 1, "pushes": [{"captured_at": T(0), "objects": [
        {"role": "whole", "sha256": entry["hash"], "uploaded_name": "Show.S02E02.mkv"}]}]}
    (folder / f"{entry['short_id']}.gpcapture.json").write_text(json.dumps(capture), encoding="utf-8")
    (obj,) = mvcommon.iter_entry_objects(EP_WHOLE, entry)
    assert obj["uploaded_name"] == "Show.S02E02.mkv"                  # what was really pushed (old untagged)
    entry["gphotos"] = mvcommon.new_gphotos_block(entry["hash"], uploaded_name="Mapped Name [x].mkv")
    (obj,) = mvcommon.iter_entry_objects(EP_WHOLE, entry)
    assert obj["uploaded_name"] == "Mapped Name [x].mkv" and obj["block"] == entry["gphotos"]
    assert obj["block"] is not entry["gphotos"]                        # a copy: callers cannot edit the library
    for untrusted in (mvcommon.new_gphotos_block(_h("before the re-master"), uploaded_name="Stale [x].mkv"),
                      dict(entry["gphotos"], v=2)):
        entry["gphotos"] = untrusted
        (obj,) = mvcommon.iter_entry_objects(EP_WHOLE, entry)
        assert obj["block"] is None and obj["uploaded_name"] == "Show.S02E02.mkv"   # P-2: ignored


def test_cloud_state_confirmed_only_from_a_trusted_block(lib):
    entry = copy.deepcopy(lib[ANIME])
    confirmed = {"tv": {"photo_id": PID[2], "upload_ms": 1, "verified_by": "filename", "confirmed": True}}
    entry["gphotos"] = mvcommon.new_gphotos_block(entry["hash"], accounts=confirmed)
    assert [o["cloud_state"] for o in mvcommon.iter_entry_objects(ANIME, entry)] == ["confirmed"]
    entry["gphotos"] = mvcommon.new_gphotos_block(_h("stale pin"), accounts=confirmed)
    assert [o["cloud_state"] for o in mvcommon.iter_entry_objects(ANIME, entry)] == ["uploaded"]
    unconfirmed = {"tv": {"photo_id": PID[2], "verified_by": "manual"}}
    entry["gphotos"] = mvcommon.new_gphotos_block(entry["hash"], accounts=unconfirmed)
    assert [o["cloud_state"] for o in mvcommon.iter_entry_objects(ANIME, entry)] == ["uploaded"]


def test_partial_chunk_push_makes_only_its_chunks_unverified(lib, tmp_path):
    entry = copy.deepcopy(lib[OTHER])
    entry.update(status="local_ready", uploaded=False, folder_path=str(tmp_path))
    first = entry["split_info"]["chunks"][0]
    capture = {"capture_version": 1, "prep": {"source": {"sha256": entry["hash"]}},
               "pushes": [{"captured_at": T(3), "chunk_range": "1-1", "complete": False, "objects": [
                   {"role": "chunk", "index": 1, "sha256": first["hash"], "uploaded_name": first["filename"]}]}]}
    (tmp_path / f"{entry['short_id']}.gpcapture.json").write_text(json.dumps(capture), encoding="utf-8")
    assert [o["cloud_state"] for o in mvcommon.iter_entry_objects(OTHER, entry)] == ["unverified", "local"]
    lib = dict(lib, **{OTHER: entry})
    assert _labels(o for o in mvcommon.iter_uploaded_objects(lib, [OTHER])) == [(OTHER, "chunk", "chunk 1/2")]


def test_a_prep_only_capture_is_not_a_push(lib, tmp_path):
    entry = copy.deepcopy(lib[LOCAL_EP])
    entry["folder_path"] = str(tmp_path)
    capture = {"capture_version": 1, "prep": {"uploaded_name": entry["search_term"],
                                              "source": {"sha256": entry["hash"], "sha1": S02E03_SHA1}}}
    (tmp_path / f"{entry['short_id']}.gpcapture.json").write_text(json.dumps(capture), encoding="utf-8")
    (obj,) = mvcommon.iter_entry_objects(LOCAL_EP, entry)
    assert obj["cloud_state"] == "local" and obj["capture"]["dedup_key"] == S02E03_KEY


# ================================================================== (e) the capture reader

def test_read_capture_v1_written_by_the_real_gpcapture_functions(tmp_path):
    folder = str(tmp_path)
    src = tmp_path / "Show.S01E01.mkv"
    src.write_bytes(b"whole master bytes")
    whole_sha = mvcommon.calculate_file_hash(str(src))                # primes the SHA-1 side-channel
    gpcapture.capture_after_prep(folder, "tv-x-s01e01", "def456", str(src), "Show.S01E01 [def456].mkv",
                                 whole_sha, {"width_height": "1920x1080", "duration_mins": 42})
    # a push in ANOTHER process: its whole-file object has no SHA-1 — it must not erase the prep's
    gpcapture.capture_after_push(folder, "tv-x-s01e01", "def456", [
        {"role": "whole", "index": None, "uploaded_name": "Show.S01E01 [def456].mkv", "sha256": whole_sha,
         "sha1": None, "dedup_key": None, "date_utc": None, "size_bytes": 18}], device_id="FAKESERIAL01",
        remote_dir="/sdcard/Media/Series/Show")
    facts = mvcommon.read_capture(folder, "def456")
    assert list(facts) == [whole_sha]
    whole = facts[whole_sha]
    assert whole["sha1"] == hashlib.sha1(b"whole master bytes").hexdigest()
    assert whole["dedup_key"] == gpweb.dedup_key(whole["sha1"])
    assert (whole["role"], whole["uploaded_name"], whole["size_bytes"]) == ("whole", "Show.S01E01 [def456].mkv", 18)
    assert (whole["width"], whole["height"]) == (1920, 1080)
    assert whole["pushed"]["device_id"] == "FAKESERIAL01" and whole["pushed"]["at"].endswith("Z")
    assert whole["remote_dir"] == "/sdcard/Media/Series/Show"
    assert "duration_s" not in whole                                   # minutes are not seconds: not derived


def test_read_capture_partial_pushes_union_and_the_latest_push_wins(tmp_path):
    a, b = _h("chunk-a"), _h("chunk-b")
    doc = {"capture_version": 1, "pushes": [
        {"captured_at": T(1), "device_id": "FAKESERIAL01", "chunk_range": "1-1", "complete": False,
         "objects": [{"role": "chunk", "index": 1, "sha256": a, "uploaded_name": "x.chunk.001.mkv"}]},
        {"captured_at": T(2), "device_id": "FAKESERIAL01", "chunk_range": "2-2", "complete": False,
         "objects": [{"role": "chunk", "index": 2, "sha256": b, "uploaded_name": "x.chunk.002.mkv"}]},
        {"captured_at": T(3), "device_id": "FAKESERIAL02", "complete": True,
         "objects": [{"role": "chunk", "index": 1, "sha256": a, "uploaded_name": "x.chunk.001.mkv"}]}]}
    (tmp_path / "s1d2.gpcapture.json").write_text(json.dumps(doc), encoding="utf-8")
    facts = mvcommon.read_capture(str(tmp_path), "s1d2")
    assert facts[a]["pushed"] == {"at": T(3), "device_id": "FAKESERIAL02"}
    assert facts[b]["pushed"] == {"at": T(2), "device_id": "FAKESERIAL01"} and facts[b]["index"] == 2


def test_read_capture_v2_keys(tmp_path):
    sha = _h("v2")
    doc = {"capture_version": 2, "future_key": {"ignored": True}, "pushes": [
        {"captured_at": T(4), "device_id": "FAKESERIAL01", "device_tz": "Asia/Singapore",
         "device_tz_offset_min": 480, "complete": True, "objects": [
             {"role": "chunk", "index": 1, "sha256": sha, "sha1": S02E03_SHA1.upper(), "width": 3840,
              "height": 2160, "duration_s": 1742.5, "approach_hint": "hash", "date_utc": "2022-02-05T11:49:46Z",
              "unknown_future_fact": 1}]}]}
    (tmp_path / "v2v2v2.gpcapture.json").write_text(json.dumps(doc), encoding="utf-8")
    f = mvcommon.read_capture(str(tmp_path), "v2v2v2")[sha]
    assert f["pushed"] == {"at": T(4), "device_id": "FAKESERIAL01", "device_tz": "Asia/Singapore",
                           "device_tz_offset_min": 480}
    assert (f["sha1"], f["dedup_key"]) == (S02E03_SHA1, S02E03_KEY)
    assert (f["width"], f["height"], f["duration_s"], f["approach_hint"]) == (3840, 2160, 1742.5, "hash")
    assert f["date_utc"] == "2022-02-05T11:49:46Z" and "unknown_future_fact" not in f


@pytest.mark.parametrize("content", [
    "not json", "[1, 2]", '{"pushes": "nope"}', '{"pushes": [1, {"objects": "x"}, {"objects": [7, {}]}]}',
    '{"prep": {"source": {"sha256": "short"}}}', '{"prep": ["x"]}', ""])
def test_read_capture_tolerates_malformed_files(tmp_path, content):
    (tmp_path / "bad000.gpcapture.json").write_text(content, encoding="utf-8")
    assert mvcommon.read_capture(str(tmp_path), "bad000") == {}


def test_read_capture_missing_inputs_and_key_only_objects(tmp_path):
    assert mvcommon.read_capture(str(tmp_path), "absent") == {}
    assert mvcommon.read_capture(None, "abc") == {} and mvcommon.read_capture(str(tmp_path), None) == {}
    sha = _h("key-only")
    doc = {"pushes": [{"objects": [{"sha256": sha, "dedup_key": S02E03_KEY, "size_bytes": -5,
                                    "approach_hint": "psychic", "date_utc": "yesterday"}]}]}
    (tmp_path / "keyonl.gpcapture.json").write_text(json.dumps(doc), encoding="utf-8")
    f = mvcommon.read_capture(str(tmp_path), "keyonl")[sha]
    assert f == {"sha256": sha, "sha1": S02E03_SHA1, "dedup_key": S02E03_KEY, "pushed": {}}


# ================================================================== (f) the learned store

def _rec(sha=None, account="tv", **kw):
    """A learned record for EP_WHOLE (or kw manual_id) — the P-10 line."""
    base = {"v": 1, "at": kw.pop("at", T(0)), "account": account, "manual_id": kw.pop("manual_id", EP_WHOLE),
            "sha256": sha or _h(EP_WHOLE), "source": kw.pop("source", "check")}
    base.update(kw)
    return base


def test_append_learned_writes_one_canonical_line_and_fsyncs(monkeypatch):
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(mvcommon.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: datetime(2026, 9, 28, 1, 2, 3, tzinfo=timezone.utc))
    written = mvcommon.append_learned("tv", {
        "stale": False, "approach": {"n": 1, "outcome": "exactly-one", "tried": "s2_exact_name", "via": "ui"},
        "proof": "filename", "photo_id": PID[0], "sha256": _h(EP_WHOLE), "manual_id": EP_WHOLE,
        "label": "chunk 2/3 \N{TAMIL LETTER A}", "source": "map", "dedup_key": gpweb.sha1_hex_to_std_b64(S02E03_SHA1),
        "alternates": [PID[3], PID[2], PID[3]], "taken_ms": S02E03_TAKEN_MS, "width": None})
    assert list(written) == ["v", "at", "account", "manual_id", "label", "sha256", "photo_id", "taken_ms",
                             "dedup_key", "proof", "source", "approach", "stale", "alternates"]
    assert written["at"] == "2026-09-28T01:02:03Z" and written["dedup_key"] == S02E03_KEY
    assert written["alternates"] == [PID[2], PID[3]] and list(written["approach"]) == ["tried", "outcome", "n", "via"]
    with open(mvcommon.learned_path("tv"), "rb") as fh:
        raw = fh.read()
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    assert json.loads(raw.decode("utf-8")) == written
    assert "\N{TAMIL LETTER A}".encode("utf-8") in raw                  # UTF-8, not \u-escaped
    assert synced, "append_learned must fsync"
    assert mvcommon.read_learned("tv") == [written]


@pytest.mark.parametrize("account,record,why", [
    ("tv", _rec(color="blue", proof="manual", photo_id=PID[0]), "unknown key"),
    ("films", _rec(account=None, proof="manual", photo_id=PID[0]), "unknown account"),
    ("movies", _rec(proof="manual", photo_id=PID[0]), "a tv record in the movies sidecar"),
    ("tv", _rec(sha="abc", proof="manual", photo_id=PID[0]), "sha256"),
    ("tv", _rec(manual_id="", proof="manual", photo_id=PID[0]), "manual_id"),
    ("tv", _rec(source="guess", proof="manual", photo_id=PID[0]), "source"),
    ("tv", _rec(v=2, proof="manual", photo_id=PID[0]), "version"),
    ("tv", _rec(at="2026-09-28 10:00:00", proof="manual", photo_id=PID[0]), "at"),
    ("tv", _rec(photo_id=PID[0]), "a photo_id needs its proof"),
    ("tv", _rec(photo_id="short", proof="filename"), "photo_id shape"),
    ("tv", _rec(proof="vibes", photo_id=PID[0]), "proof"),
    ("tv", _rec(stale=True), "stale needs its photo_id"),
    ("tv", _rec(stale="yes", photo_id=PID[0]), "stale bool"),
    ("tv", _rec(search_override="my term"), "search_override is manual only"),
    ("tv", _rec(search_override="  ", source="manual"), "empty search_override"),
    ("tv", _rec(approach={"tried": "s9_magic", "outcome": "exactly-one"}), "stage"),
    ("tv", _rec(approach={"tried": "s2_exact_name", "outcome": "maybe"}), "outcome"),
    ("tv", _rec(approach={"tried": "s2_exact_name", "outcome": "ambiguous", "n": -1}), "n"),
    ("tv", _rec(approach={"tried": "s2_exact_name", "outcome": "ambiguous", "via": "fax"}), "via"),
    ("tv", _rec(approach={"tried": "s2_exact_name", "outcome": "ambiguous", "extra": 1}), "approach key"),
    ("tv", _rec(approach="s2"), "approach shape"),
    ("tv", _rec(proof="filename", photo_id=PID[0], dedup_key="nope"), "dedup_key"),
    ("tv", _rec(proof="filename", photo_id=PID[0], taken_ms="1644061786000"), "taken_ms"),
    ("tv", _rec(proof="filename", photo_id=PID[0], upload_ms=-1), "upload_ms"),
    ("tv", _rec(proof="filename", photo_id=PID[0], width=-1), "width"),
    ("tv", _rec(proof="filename", photo_id=PID[0], gp_filename=""), "gp_filename"),
    ("tv", _rec(proof="filename", photo_id=PID[0], alternates=PID[1]), "alternates shape"),
    ("tv", _rec(proof="filename", photo_id=PID[0], original_quality="yes"), "flag"),
    ("tv", _rec(label=7, proof="filename", photo_id=PID[0]), "label"),
    ("tv", _rec(), "no fact at all"),
    ("tv", ["not", "a", "dict"], "shape"),
])
def test_append_learned_refuses_invalid_records(account, record, why):
    with pytest.raises(ValueError):
        mvcommon.append_learned(account, record)
    assert not os.path.exists(mvcommon.learned_path("tv")), why


def test_read_learned_is_torn_line_tolerant_and_never_glues_onto_a_torn_line():
    path = mvcommon.learned_path("tv")
    os.makedirs(os.path.dirname(path))
    good = mvcommon._normalize_learned(_rec(proof="filename", photo_id=PID[0]), "tv")
    with open(path, "wb") as fh:
        fh.write(json.dumps(good).encode() + b"\r\n")                  # an editor's CRLF
        fh.write(b"\n   \n")                                            # blank lines
        fh.write(json.dumps(_rec(account="movies", proof="filename", photo_id=PID[1])).encode() + b"\n")
        fh.write(b'{"v": 1, "at": "2026-09-2')                          # a crash mid-append: no newline
    appended = mvcommon.append_learned("tv", _rec(at=T(9), proof="dedupkey", photo_id=PID[2]))
    errors = []
    assert mvcommon.read_learned("tv", errors) == [good, appended]
    assert [e["line"] for e in errors] == [4, 5]
    assert "movies" in errors[0]["error"] and "torn" in errors[1]["error"]
    assert mvcommon.read_learned("anime") == []
    with pytest.raises(ValueError):
        mvcommon.read_learned("films")


def test_read_learned_accepts_a_sidecar_an_editor_saved_with_a_bom():
    record = mvcommon._normalize_learned(_rec(proof="filename", photo_id=PID[0]), "tv")
    path = mvcommon.learned_path("tv")
    os.makedirs(os.path.dirname(path))
    with open(path, "wb") as fh:
        fh.write(b"\xef\xbb\xbf" + json.dumps(record).encode("utf-8") + b"\r\n")
    errors = []
    assert mvcommon.read_learned("tv", errors) == [record] and errors == []


def test_read_learned_all_accounts_in_account_order():
    tv = mvcommon.append_learned("tv", _rec(proof="filename", photo_id=PID[0]))
    movies = mvcommon.append_learned("movies", _rec(account="movies", proof="filename", photo_id=PID[1]))
    others = mvcommon.append_learned("others", _rec(account="others", manual_id=OTHER, proof="manual",
                                                    photo_id=PID[2]))
    assert mvcommon.read_learned() == [movies, tv, others]


def test_read_learned_propagates_a_real_read_error(monkeypatch):
    mvcommon.append_learned("tv", _rec(proof="filename", photo_id=PID[0]))
    real_open = open

    def denied(path, *a, **kw):
        if str(path).endswith("learned_tv.jsonl"):
            raise PermissionError("locked")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", denied)
    with pytest.raises(PermissionError):
        mvcommon.read_learned("tv")


# ---------------------------------------------------------------- reduce_learned

def _reduce(records, account="tv", sha=None):
    return mvcommon.reduce_learned(records)[(account, sha or _h(EP_WHOLE))]


def test_reduce_follows_the_p5_proof_ladder_then_recency():
    records = [_rec(at=T(1), proof="dedupkey", photo_id=PID[0], upload_ms=1),
               _rec(at=T(9), proof="filename", photo_id=PID[1], upload_ms=2, source="map"),
               _rec(at=T(5), proof="manual", photo_id=PID[2], source="manual")]
    red = _reduce(records)
    assert (red["photo_id"], red["proof"], red["at"], red["upload_ms"]) == (PID[0], "dedupkey", T(1), 1)
    assert red["alternates"] == [PID[1], PID[2]]
    assert _reduce([_rec(at=T(1), proof="filename", photo_id=PID[0]), _rec(at=T(2), proof="sha256",
                                                                         photo_id=PID[1])])["photo_id"] == PID[1]
    assert _reduce([_rec(at=T(3), proof="filename", photo_id=PID[0]), _rec(at=T(2), proof="filename",
                                                                         photo_id=PID[1])])["photo_id"] == PID[0]
    assert _reduce([_rec(at=T(2), proof="filename", photo_id=PID[1]), _rec(at=T(2), proof="filename",
                                                                         photo_id=PID[0])])["photo_id"] == PID[0]


def test_reduce_prefers_a_photo_id_and_completes_its_item_facts():
    records = [_rec(at=T(1), proof="filename", photo_id=PID[0], gp_filename="Show.S02E02 [x].mkv",
                    taken_ms=5, source="map"),
               _rec(at=T(2), proof="sha256", source="fetch"),               # bytes matched, item unknown
               _rec(at=T(3), proof="filename", photo_id=PID[0], upload_ms=77, original_quality=True)]
    red = _reduce(records)
    assert (red["photo_id"], red["at"], red["upload_ms"]) == (PID[0], T(3), 77)
    assert (red["gp_filename"], red["taken_ms"], red["original_quality"]) == ("Show.S02E02 [x].mkv", 5, True)
    bare = _reduce([_rec(at=T(2), proof="sha256", source="fetch"), _rec(at=T(3), proof="manual",
                                                                        source="manual", alternates=[PID[4]])])
    assert "photo_id" not in bare and (bare["proof"], bare["source"]) == ("sha256", "fetch")
    assert bare["alternates"] == [PID[4]]


def test_reduce_stale_supersedes_older_records_for_its_photo_id():
    records = [_rec(at=T(1), proof="dedupkey", photo_id=PID[0], upload_ms=1),
               _rec(at=T(2), proof="filename", photo_id=PID[1], source="map"),
               _rec(at=T(3), stale=True, photo_id=PID[0], source="fetch")]
    red = _reduce(records)
    assert red["photo_id"] == PID[1] and red["dead"] == {PID[0]: T(3)}
    assert "alternates" not in red                                     # the dead id is nobody's alternate
    assert red["approach"] == {"failed": ["photo_id"], "at": T(3)}      # stale = direct access failed
    # a NEWER record revives the photo_id: only records older than the stale one are superseded
    red = _reduce(records + [_rec(at=T(4), proof="dedupkey", photo_id=PID[0], source="check")])
    assert red["photo_id"] == PID[0] and red["at"] == T(4) and "dead" not in red
    assert red["alternates"] == [PID[1]]


def test_reduce_stale_is_account_wide_by_photo_id():
    other_sha = _h(EP_SPLIT)
    records = [_rec(at=T(1), proof="filename", photo_id=PID[0], source="map"),
               _rec(sha=other_sha, manual_id=EP_SPLIT, at=T(2), stale=True, photo_id=PID[0], source="fetch"),
               _rec(account="movies", at=T(1), proof="filename", photo_id=PID[0], source="map")]
    reduced = mvcommon.reduce_learned(records)
    assert "photo_id" not in reduced[("tv", _h(EP_WHOLE))]
    assert reduced[("tv", _h(EP_WHOLE))]["dead"] == {PID[0]: T(2)}
    assert reduced[("movies", _h(EP_WHOLE))]["photo_id"] == PID[0]     # another account is untouched


def test_reduce_content_key_from_a_download_excludes_other_bytes():
    ours, theirs = _key("our bytes"), _key("a different upload with the same name")
    records = [_rec(at=T(1), proof="filename", photo_id=PID[0], dedup_key=theirs, source="map"),
               _rec(at=T(2), proof="filename", photo_id=PID[1], dedup_key=ours, source="map"),
               _rec(at=T(3), proof="sha256", dedup_key=ours, source="fetch")]
    red = _reduce(records)
    assert (red["photo_id"], red["content_key"]) == (PID[1], ours)
    assert "alternates" not in red                                     # PID[0] holds other bytes


def test_reduce_folds_approach_verdicts_newest_first_errors_ignored():
    records = [
        _rec(at=T(1), approach={"tried": "s2_exact_name", "outcome": "exactly-one", "via": "ui"}),
        _rec(at=T(2), approach={"tried": "s3_title_sid", "outcome": "ambiguous", "n": 4}),
        _rec(at=T(3), approach={"tried": "s0_hash", "outcome": "exactly-one", "via": "rpc"}),
        _rec(at=T(4), approach={"tried": "s0_hash", "outcome": "error"}),              # no verdict
        _rec(at=T(5), approach={"tried": "s4_legacy", "outcome": "wrong-item"}),
        _rec(at=T(6), approach={"tried": "s3u_user_query", "outcome": "exactly-one"}),
        _rec(at=T(7), approach={"tried": "s1_taken_second", "outcome": "not-found"}),
    ]
    red = _reduce(records)
    assert red["approach"] == {"best": "hash", "ok": ["hash", "exact_name", "user_query"],
                               "failed": ["taken_second", "title_sid", "legacy"], "at": T(7), "via": "rpc"}
    red = _reduce(records + [_rec(at=T(8), approach={"tried": "s0_hash", "outcome": "not-found"})])
    assert red["approach"]["best"] == "exact_name" and red["approach"]["via"] == "ui"
    assert "hash" in red["approach"]["failed"]


def test_reduce_search_override_labels_grouping_and_invalid_input():
    records = [_rec(at=T(1), source="manual", search_override="first term", label="whole"),
               _rec(at=T(2), source="manual", search_override="better term"),
               _rec(sha=_h("other"), at=T(1), proof="filename", photo_id=PID[5], source="map"),
               {"v": 1, "garbage": True}, "not a record", None]
    reduced = mvcommon.reduce_learned(records)
    assert set(reduced) == {("tv", _h(EP_WHOLE)), ("tv", _h("other"))}
    red = reduced[("tv", _h(EP_WHOLE))]
    assert red["search_override"] == {"query": "better term", "at": T(2)} and red["label"] == "whole"
    assert mvcommon.reduce_learned([]) == {} and mvcommon.reduce_learned(None) == {}


def test_reduce_is_independent_of_input_order_for_distinct_times():
    records = [_rec(at=T(i), proof=p, photo_id=PID[i % 4], upload_ms=i, source="map",
                    approach={"tried": s, "outcome": o})
               for i, (p, s, o) in enumerate([("filename", "s2_exact_name", "exactly-one"),
                                              ("manual", "s3_title_sid", "ambiguous"),
                                              ("dedupkey", "s0_photo_id", "exactly-one"),
                                              ("sha256", "s1_taken_second", "not-found"),
                                              ("filename", "s2_exact_name", "not-found")])]
    records.append(_rec(at=T(9), stale=True, photo_id=PID[2], source="fetch"))
    expected = mvcommon.reduce_learned(records)
    rng = random.Random(25)
    for _ in range(20):
        shuffled = records[:]
        rng.shuffle(shuffled)
        assert mvcommon.reduce_learned(shuffled) == expected


# ---------------------------------------------------------------- apply_learned

def _block(sha=None, **facts):
    return mvcommon.new_gphotos_block(sha or _h(EP_WHOLE), **facts)


def _apply(block, records, account="tv"):
    return mvcommon.apply_learned(block, _reduce(records, account), account)


def test_apply_creates_a_block_and_confirms_a_verified_map_record():
    red_records = [_rec(at=T(1), proof="filename", photo_id=PID[0], upload_ms=9, taken_ms=5, source="map",
                        approach={"tried": "s2_exact_name", "outcome": "exactly-one", "via": "ui"})]
    block, changed, conflict = _apply(None, red_records)
    assert (changed, conflict) == (True, None) and mvcommon.block_trusted(block, _h(EP_WHOLE))
    assert block["accounts"]["tv"] == {
        "photo_id": PID[0], "taken_ms": 5, "upload_ms": 9, "verified_at": T(1), "verified_by": "filename",
        "source": "map", "approach": {"best": "exact_name", "ok": ["exact_name"], "at": T(1), "via": "ui"},
        "confirmed": True}
    assert "home_account" not in block                                  # the engine's job (P-4)


def test_apply_is_pure_and_idempotent():
    start = _block(uploaded_name="Show.S02E02 [x].mkv", accounts={"tv": {"photo_id": PID[1],
                                                                         "verified_by": "manual"}})
    frozen = copy.deepcopy(start)
    records = [_rec(at=T(1), proof="dedupkey", photo_id=PID[0], upload_ms=1)]
    once, changed, _ = _apply(start, records)
    assert changed and start == frozen                                  # the input block is never modified
    twice, changed_again, conflict = _apply(once, records)
    assert (twice, changed_again, conflict) == (once, False, None)
    assert once["accounts"]["tv"]["alternates"] == [PID[1]]


@pytest.mark.parametrize("block,account,code", [
    (_block(_h("bytes before the re-split")), "tv", "pin-mismatch"),
    (dict(_block(), v=2), "tv", "block-version"),
    (_block(), "movies", "account-mismatch"),
    (dict(_block(), accounts=["tv"]), "tv", "malformed-block"),
    (dict(_block(), accounts={"tv": "x"}), "tv", "malformed-block"),
    ("junk", "tv", "pin-mismatch"),
])
def test_apply_conflicts_return_the_block_unchanged(block, account, code):
    before = copy.deepcopy(block)
    red = _reduce([_rec(proof="filename", photo_id=PID[0])])
    assert mvcommon.apply_learned(block, red, account) == (block, False, code)
    assert block == before
    assert mvcommon.apply_learned(block, {"account": "tv"}, "tv") == (block, False, "invalid-record")


def test_apply_refuses_an_item_that_holds_other_bytes():
    ours, theirs = _key("capture bytes"), _key("re-split twin")
    captured = _block(dedup_key=ours)
    assert _apply(captured, [_rec(proof="filename", photo_id=PID[0], dedup_key=theirs)]) \
        == (captured, False, "dedup-key-mismatch")
    # a download's content key that contradicts the capture's
    assert _apply(captured, [_rec(proof="sha256", dedup_key=theirs, source="fetch")]) \
        == (captured, False, "content-key-mismatch")
    # the account already holds an item with other bytes and the record brings no identity: report it
    held = _block(dedup_key=ours, accounts={"tv": {"photo_id": PID[0], "dedup_key": theirs,
                                                   "verified_by": "filename"}})
    assert _apply(held, [_rec(approach={"tried": "s2_exact_name", "outcome": "ambiguous"})]) \
        == (held, False, "dedup-key-mismatch")
    # ... a record with a consistent identity replaces it — and the wrong item is NOT an alternate
    fixed, changed, conflict = _apply(held, [_rec(proof="dedupkey", photo_id=PID[1], dedup_key=ours,
                                                  upload_ms=3)])
    assert (changed, conflict) == (True, None)
    assert fixed["accounts"]["tv"]["photo_id"] == PID[1] and "alternates" not in fixed["accounts"]["tv"]


def test_apply_compares_dedup_keys_in_canonical_form():
    """A held account record whose dedupKey is spelled differently (standard base64, padded — e.g. a
    hand-edited block) is the SAME content, not a wrong item."""
    spelled = gpweb.sha1_hex_to_std_b64(S02E03_SHA1)
    held = _block(sha1=S02E03_SHA1, accounts={"tv": {"photo_id": PID[0], "dedup_key": spelled,
                                                     "verified_by": "filename", "verified_at": T(1)}})
    new, changed, conflict = _apply(held, [_rec(at=T(2), approach={"tried": "s0_photo_id",
                                                                   "outcome": "exactly-one"})])
    assert conflict is None and changed and new["accounts"]["tv"]["photo_id"] == PID[0]


def test_apply_learns_the_content_key_from_a_download():
    block, changed, conflict = _apply(_block(), [_rec(proof="sha256", dedup_key=S02E03_KEY, source="fetch")])
    assert (changed, conflict) == (True, None)
    assert (block["sha1"], block["dedup_key"]) == (S02E03_SHA1, S02E03_KEY)
    assert block["accounts"]["tv"] == {"verified_at": T(0), "verified_by": "sha256", "source": "fetch"}
    assert list(block)[:4] == ["v", "sha256", "sha1", "dedup_key"]      # P-1 order kept


def test_apply_identity_merge_rules():
    base = _block(accounts={"tv": {"photo_id": PID[0], "upload_ms": 1, "gp_filename": "a.mkv",
                                   "verified_at": T(5), "verified_by": "filename", "source": "map"}})
    better, *_ = _apply(base, [_rec(at=T(1), proof="dedupkey", photo_id=PID[1], upload_ms=2)])
    acc = better["accounts"]["tv"]
    assert (acc["photo_id"], acc["verified_by"], acc["alternates"]) == (PID[1], "dedupkey", [PID[0]])
    assert "gp_filename" not in acc                                     # facts of the replaced item go with it
    weaker, *_ = _apply(base, [_rec(at=T(9), proof="manual", photo_id=PID[2], source="manual")])
    assert weaker["accounts"]["tv"]["photo_id"] == PID[0] and weaker["accounts"]["tv"]["alternates"] == [PID[2]]
    # the same item seen again: newer facts refresh, the stronger proof is kept
    same, *_ = _apply(better, [_rec(at=T(8), proof="filename", photo_id=PID[1], upload_ms=5, source="map",
                                    original_quality=False)])
    acc = same["accounts"]["tv"]
    assert (acc["upload_ms"], acc["original_quality"], acc["verified_by"], acc["verified_at"]) == (
        5, False, "dedupkey", T(1))
    older, *_ = _apply(same, [_rec(at=T(0), proof="dedupkey", photo_id=PID[1], upload_ms=99)])
    assert older["accounts"]["tv"]["upload_ms"] == 5                     # older facts never overwrite


def test_apply_drops_a_dead_photo_id_and_its_confirmation():
    confirmed, *_ = _apply(None, [_rec(at=T(1), proof="dedupkey", photo_id=PID[0], upload_ms=1,
                                       alternates=[PID[3]])])
    assert confirmed["accounts"]["tv"]["confirmed"] is True
    stale_record = [_rec(at=T(2), stale=True, photo_id=PID[0], source="fetch"),
                    _rec(at=T(2), stale=True, photo_id=PID[3], source="fetch")]
    dropped, changed, conflict = _apply(confirmed, stale_record)
    assert (changed, conflict) == (True, None)
    assert dropped["accounts"]["tv"] == {"approach": {"failed": ["photo_id"], "at": T(2)}}
    # a stale record OLDER than the held verification does not drop it
    kept, *_ = _apply(confirmed, [_rec(at=T(0), stale=True, photo_id=PID[0], source="fetch")])
    assert kept["accounts"]["tv"]["photo_id"] == PID[0] and kept["accounts"]["tv"]["confirmed"] is True


@pytest.mark.parametrize("record,confirmed", [
    (_rec(proof="filename", photo_id=PID[0], upload_ms=1, source="map"), True),
    (_rec(proof="dedupkey", photo_id=PID[0], upload_ms=1, source="check"), True),
    (_rec(proof="sha256", photo_id=PID[0], upload_ms=1, source="fetch"), True),
    (_rec(proof="dedupkey", photo_id=PID[0], upload_ms=1, source="confirm"), True),
    (_rec(proof="manual", photo_id=PID[0], upload_ms=1, source="manual"), False),   # needs a check first
    (_rec(proof="dedupkey", photo_id=PID[0], upload_ms=1, source="manual"), False),  # resolve_photos alone
    (_rec(proof="dedupkey", photo_id=PID[0], source="check"), False),               # never seen uploaded
    (_rec(proof="sha256", source="fetch"), False),                                  # no item
])
def test_confirmed_needs_a_verified_item_seen_uploaded(record, confirmed):
    block, *_ = _apply(None, [record])
    assert block["accounts"]["tv"].get("confirmed", False) is confirmed


def test_apply_merges_approaches_by_recency_and_keeps_unknown_keys():
    base = _block()
    base["future_block_key"] = {"kept": True}
    base["accounts"] = {"tv": {"approach": {"best": "exact_name", "ok": ["exact_name", "teleport"],
                                            "failed": ["title_sid"], "at": T(5), "via": "ui"},
                               "future_account_key": 1},
                        "movies": {"verified_by": "filename"}}
    newer, *_ = _apply(base, [_rec(at=T(6), approach={"tried": "s3_title_sid", "outcome": "exactly-one",
                                                      "via": "rpc"})])
    assert newer["accounts"]["tv"]["approach"] == {"best": "exact_name", "ok": ["exact_name", "title_sid",
                                                                                "teleport"],
                                                   "at": T(6), "via": "ui"}
    assert newer["future_block_key"] == {"kept": True} and newer["accounts"]["tv"]["future_account_key"] == 1
    assert list(newer["accounts"]) == ["movies", "tv"]                  # GP_ACCOUNTS order
    older, *_ = _apply(base, [_rec(at=T(1), approach={"tried": "s2_exact_name", "outcome": "not-found"})])
    assert older["accounts"]["tv"]["approach"]["ok"] == ["exact_name", "teleport"]  # older evidence loses


def test_apply_search_override_newer_wins_and_raw_records_are_reduced():
    block, *_ = mvcommon.apply_learned(None, _rec(at=T(2), source="manual", search_override="new term"), "tv")
    assert block["search_override"] == {"query": "new term", "at": T(2)}
    kept, changed, _ = mvcommon.apply_learned(block, _rec(at=T(1), source="manual", search_override="old"), "tv")
    assert kept["search_override"]["query"] == "new term" and not changed


def test_learned_round_trip_to_a_confirmed_object_and_its_home_account(lib):
    """The whole Step 3 chain as Steps 6/9/10 will use it: producers append -> read -> reduce -> apply ->
    the object is confirmed and routes to its real account (the X-Files-in-MOVIES case, IMP-C26)."""
    obj = next(mvcommon.iter_entry_objects(EP_WHOLE, lib[EP_WHOLE]))
    mvcommon.append_learned("movies", {"manual_id": EP_WHOLE, "sha256": obj["sha256"], "label": obj["label"],
                                       "source": "map", "proof": "filename", "photo_id": PID[7], "upload_ms": 3,
                                       "gp_filename": obj["uploaded_name"]})
    mvcommon.append_learned("tv", {"manual_id": EP_WHOLE, "sha256": obj["sha256"], "source": "check",
                                   "approach": {"tried": "s2_exact_name", "outcome": "not-found", "n": 0}})
    reduced = mvcommon.reduce_learned(mvcommon.read_learned())
    block = None
    for account in mvcommon.GP_ACCOUNTS:
        red = reduced.get((account, obj["sha256"]))
        if red:
            block, changed, conflict = mvcommon.apply_learned(block, red, account)
            assert changed and conflict is None
    stored = dict(block)
    stored["home_account"] = mvcommon.home_account(block, EP_WHOLE, mainfetch.ID_PREFIX_PROFILE,
                                                   mainfetch.DEFAULT_PROFILE)
    assert stored["home_account"] == "movies"
    entry = dict(lib[EP_WHOLE], gphotos=mvcommon.new_gphotos_block(obj["sha256"], **{
        k: v for k, v in stored.items() if k not in ("v", "sha256")}))
    (seen,) = mvcommon.iter_entry_objects(EP_WHOLE, entry)
    assert seen["cloud_state"] == "confirmed" and seen["block"]["accounts"]["movies"]["photo_id"] == PID[7]
    assert seen["block"]["accounts"]["tv"] == {"approach": {"failed": ["exact_name"], "at": seen["block"][
        "accounts"]["tv"]["approach"]["at"]}}


# ================================================================== (f) the failed list

def _failed(**kw):
    record = {"account": "tv", "manual_id": EP_WHOLE, "sha256": _h(EP_WHOLE), "label": "whole",
              "reason": "not-found", "run": "check-20260928T100000"}
    record.update(kw)
    return record


def test_append_failed_fills_and_validates(monkeypatch):
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: datetime(2026, 9, 28, 1, 2, 3, tzinfo=timezone.utc))
    written = mvcommon.append_failed(_failed(
        tried=[{"n": 3, "outcome": "ambiguous", "approach": "exact_name"}, {"approach": "legacy",
                                                                           "outcome": "not-found"}],
        candidates=[PID[0], PID[1], PID[0]], expected_taken_local="2022-02-05 19:49:46 GMT+08:00"))
    assert list(written) == ["at", "run", "account", "category", "manual_id", "label", "sha256",
                             "expected_taken_local", "tried", "candidates", "reason"]
    assert (written["at"], written["category"]) == ("2026-09-28T01:02:03Z", "tv")
    assert written["tried"][0] == {"approach": "exact_name", "outcome": "ambiguous", "n": 3}
    assert written["candidates"] == [PID[0], PID[1]]
    assert mvcommon.read_failed() == [written]
    assert mvcommon.append_failed(_failed(manual_id="zzz-unknown"))["manual_id"] == "zzz-unknown"


@pytest.mark.parametrize("record", [
    _failed(reason="gave-up"), _failed(account="films"), _failed(manual_id=""), _failed(sha256="abc"),
    _failed(category="sports"), _failed(label=5), _failed(tried="exact_name"),
    _failed(tried=[{"approach": "s2_exact_name", "outcome": "not-found"}]),      # a stage, not an approach
    _failed(tried=[{"approach": "exact_name", "outcome": "lost"}]),
    _failed(tried=[{"approach": "exact_name", "outcome": "ambiguous", "n": -2}]),
    _failed(tried=[{"approach": "exact_name", "outcome": "ambiguous", "why": "x"}]),
    _failed(candidates=["x"]), _failed(colour="red"), _failed(at="yesterday"), "nope"])
def test_append_failed_refuses_invalid_records(record):
    with pytest.raises(ValueError):
        mvcommon.append_failed(record)
    assert not os.path.exists(mvcommon.failed_path())


def test_read_failed_is_torn_line_tolerant():
    first = mvcommon.append_failed(_failed())
    with open(mvcommon.failed_path(), "ab") as fh:
        fh.write(b'{"account": "tv", "manual_id": "x", "reason": "not-found"}\n{"torn')
    second = mvcommon.append_failed(_failed(reason="ambiguous"))
    errors = []
    assert mvcommon.read_failed(errors) == [first, second]
    assert [e["line"] for e in errors] == [2, 3]


# ---------------------------------------------------------------- failed_objects.csv

def _csv_rows(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    assert raw.startswith(b"\xef\xbb\xbf")                                # UTF-8 BOM (P-18)
    assert raw.count(b"\r\n") == raw.count(b"\n")                         # CRLF only
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline="")))


def test_export_failed_csv_rows_dedupe_drop_and_commands(lib):
    confirmed_lib = copy.deepcopy(lib)
    anime = confirmed_lib[ANIME]
    anime["gphotos"] = mvcommon.new_gphotos_block(anime["hash"], accounts={
        "anime": {"photo_id": PID[9], "upload_ms": 1, "verified_by": "dedupkey", "confirmed": True}})
    chunk2 = lib[EP_SPLIT]["split_info"]["chunks"][1]
    bts = lib[MOVIE]["extras"]["groups"]["Specials"]["items"][0]
    mvcommon.append_failed(_failed(at=T(1), reason="ambiguous"))
    mvcommon.append_failed(_failed(at=T(2), reason="not-found",                               # newest wins
                                   tried=[{"approach": "exact_name", "outcome": "not-found", "n": 0}]))
    mvcommon.append_failed(_failed(manual_id=EP_SPLIT, sha256=chunk2["hash"], label="chunk 2/2",
                                   reason="probable", candidates=[PID[4]], at=T(3)))
    mvcommon.append_failed(_failed(account="movies", manual_id=MOVIE, sha256=bts["hash"], reason="s4-skipped",
                                   label="extra Specials/BTS.mkv", at=T(3)))
    mvcommon.append_failed(_failed(account="anime", manual_id=ANIME, sha256=anime["hash"], at=T(3)))  # confirmed
    mvcommon.append_failed(_failed(manual_id=EP_WHOLE, sha256=_h("pre-re-master bytes"), at=T(3)))    # orphan
    mvcommon.append_failed(_failed(manual_id="tv-en-1999-gone-s01e01", sha256=_h("gone"), at=T(3)))   # orphan
    result = mvcommon.export_failed_csv(confirmed_lib)
    assert result == {"path": mvcommon.failed_csv_path(), "rows": 3, "dropped_confirmed": 1, "dropped_orphans": 2}
    rows = _csv_rows(result["path"])
    assert list(rows[0]) == list(mvcommon.GP_FAILED_CSV_COLUMNS)
    assert [(r["account"], r["manual_id"], r["object"], r["status"]) for r in rows] == [
        ("movies", MOVIE, "extra Specials/BTS.mkv", "s4-skipped"),
        ("tv", EP_WHOLE, "whole", "not-found"),
        ("tv", EP_SPLIT, "chunk 2/2", "probable")]
    extra, whole, chunk = rows
    assert extra["set_search_cmd"] == ""                                 # an extra's fix is new_search_term
    assert whole["set_search_cmd"] == f'python main.py set_search {EP_WHOLE} "<new search term>"'
    assert whole["candidates"] == "exact_name=not-found(0)" and whole["category"] == "tv"
    assert whole["uploaded_name"] == lib[EP_WHOLE]["search_term"]         # filled from the library object
    assert chunk["suggested_photo_url"] == "https://photos.google.com/photo/" + PID[4]
    assert chunk["candidates"] == "candidates: " + PID[4] and chunk["sha256"] == chunk2["hash"]
    assert all(r[c] == "" for r in rows for c in ("decision", "photo_url", "date", "time", "gmt",
                                                  "new_search_term", "note"))
    assert not [n for n in os.listdir(mvcommon.GPHOTOS_STATE_DIR) if n.endswith(".tmp")]


def test_export_failed_csv_with_no_failures_writes_just_the_header(lib):
    assert mvcommon.export_failed_csv(lib)["rows"] == 0
    assert _csv_rows(mvcommon.failed_csv_path()) == []


def test_export_failed_csv_matches_a_hashless_record_by_label(lib):
    mvcommon.append_failed({"account": "tv", "manual_id": EP_SPLIT, "label": "chunk 1/2", "reason": "not-found"})
    (row,) = _csv_rows(mvcommon.export_failed_csv(lib)["path"])
    assert row["sha256"] == lib[EP_SPLIT]["split_info"]["chunks"][0]["hash"]


def test_export_failed_csv_gives_one_row_per_object_however_its_records_name_it(lib):
    chunk = lib[EP_SPLIT]["split_info"]["chunks"][0]
    mvcommon.append_failed(_failed(manual_id=EP_SPLIT, sha256=chunk["hash"], label="chunk 1/2", at=T(1)))
    mvcommon.append_failed({"account": "tv", "manual_id": EP_SPLIT, "label": "chunk 1/2", "at": T(2),
                            "reason": "ambiguous"})                     # same object, named by label only
    (row,) = _csv_rows(mvcommon.export_failed_csv(lib)["path"])
    assert (row["status"], row["sha256"]) == ("ambiguous", chunk["hash"])


def test_write_failed_csv_is_atomic_when_the_file_is_locked(tmp_path, monkeypatch):
    target = tmp_path / "failed_objects.csv"
    target.write_text("previous", encoding="utf-8")

    def locked(src, dst):
        raise PermissionError("open in Excel")

    monkeypatch.setattr(mvcommon.os, "replace", locked)
    with pytest.raises(PermissionError):
        mvcommon.write_failed_csv(str(target), [{"account": "tv"}])
    assert target.read_text(encoding="utf-8") == "previous"
    assert os.listdir(tmp_path) == ["failed_objects.csv"]                  # the temp file was removed


def _edited_csv(tmp_path, rows, header=None, delimiter=",", encoding="utf-8-sig", newline="\r\n", sep_line=False):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=delimiter, lineterminator=newline)
    columns = header or list(mvcommon.GP_FAILED_CSV_COLUMNS)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c.split(" (")[0].strip().lower(), "") for c in columns])
    text = (f"sep={delimiter}{newline}" if sep_line else "") + buf.getvalue()
    path = tmp_path / "edited.csv"
    path.write_bytes(text.encode(encoding))
    return str(path)


def _edit(decision, **kw):
    row = {"account": "tv", "category": "tv", "manual_id": EP_WHOLE, "object": "whole", "sha256": _h(EP_WHOLE),
           "decision": decision}
    row.update(kw)
    return row


def test_read_failed_csv_round_trips_every_decision(tmp_path, lib):
    mvcommon.append_failed(_failed())
    exported = _csv_rows(mvcommon.export_failed_csv(lib)["path"])
    rows = [dict(exported[0], decision="url", photo_url=f"https://photos.google.com/u/1/photo/{PID[0]}?x=1"),
            dict(exported[0], decision="DateTime", date="2022-02-05", time="7:49:46 PM", gmt="+08:00",
                 note="from the user's screenshot"),
            dict(exported[0], decision="search", new_search_term="  silicon valley 0003a8 "),
            dict(exported[0], decision="not_uploaded"), dict(exported[0], decision="skip"),
            dict(exported[0]),                                                          # not edited
            {c: "" for c in mvcommon.GP_FAILED_CSV_COLUMNS}]                           # an Excel blank row
    edits, errors = mvcommon.read_failed_csv(_edited_csv(tmp_path, rows))
    assert errors == []
    assert [e["decision"] for e in edits] == ["url", "datetime", "search", "not_uploaded", "skip"]
    url, when, search, _, _ = edits
    assert url["photo_id"] == PID[0] and url["sha256"] == _h(EP_WHOLE) and url["account"] == "tv"
    assert (when["taken_local"], when["gmt_offset_min"], when["taken_ms"]) == (
        "2022-02-05T19:49:46", 480, S02E03_TAKEN_MS)                        # the published S02E03 instant
    assert when["note"] == "from the user's screenshot" and when["line"] == 3
    assert search["search_term"] == "silicon valley 0003a8"
    assert {"manual_id", "object", "uploaded_name", "category"} <= set(url)


@pytest.mark.parametrize("date_text,time_text,gmt,expected_local", [
    ("5 Feb 2022", "19:49:46", "GMT+08:00", "2022-02-05T19:49:46"),
    ("Feb 5, 2022", "19:49", "UTC+8", "2022-02-05T19:49:00"),
    ("21 Sept 2026", "09:00:20", "+0800", "2026-09-21T09:00:20"),     # the UK account's "Sept"
    ("2022/02/05", "7:49 p.m.", "+08:00", "2022-02-05T19:49:00"),
    ("2022-02-05 00:00:00", "7:49:46\N{NARROW NO-BREAK SPACE}PM", "+08:00", "2022-02-05T19:49:46"),
    ("13/02/2022", "12:00 AM", "GMT", "2022-02-13T00:00:00"),         # day > 12: only one reading
    ("02/13/2022", "12:30 PM", "-05:30", "2022-02-13T12:30:00"),      # month/day, still unambiguous
    ("05/05/2022", "00:00", "+00:00", "2022-05-05T00:00:00"),         # day == month
])
def test_read_failed_csv_tolerates_excel_date_time_and_gmt_formats(tmp_path, date_text, time_text, gmt,
                                                                   expected_local):
    (edit,), errors = mvcommon.read_failed_csv(_edited_csv(tmp_path, [
        _edit("datetime", date=date_text, time=time_text, gmt=gmt)]))
    assert errors == [] and edit["taken_local"] == expected_local


@pytest.mark.parametrize("row,message", [
    (_edit("datetime", date="05/02/2022", time="19:49", gmt="+08:00"), "ambiguous"),
    (_edit("datetime", date="yesterday", time="19:49", gmt="+08:00"), "not a date"),
    (_edit("datetime", date="2022-02-30", time="19:49", gmt="+08:00"), "day is out of range"),
    (_edit("datetime", date="2022-02-05", time="0.826", gmt="+08:00"), "not a time"),
    (_edit("datetime", date="2022-02-05", time="19:49", gmt="8:00:00 AM"), "not a UTC offset"),
    (_edit("datetime", date="2022-02-05", time="19:49"), "needs date, time and gmt"),
    (_edit("url", photo_url="https://example.com/not-a-photo"), "photo_url"),
    (_edit("url"), "photo_url"),
    (_edit("search", new_search_term="   "), "new_search_term"),
    (_edit("maybe"), "decision"),
    (_edit("skip", account="films"), "account"),
    (_edit("skip", manual_id=""), "manual_id"),
    (_edit("skip", sha256="edited by hand"), "sha256"),
])
def test_read_failed_csv_reports_invalid_edits_and_never_returns_them(tmp_path, row, message):
    rows, errors = mvcommon.read_failed_csv(_edited_csv(tmp_path, [row, _edit("skip")]))
    assert [r["decision"] for r in rows] == ["skip"]
    (error,) = errors
    assert message in error["error"] and error["line"] == 2


def test_read_failed_csv_accepts_a_bare_photo_id(tmp_path):
    (edit,), _ = mvcommon.read_failed_csv(_edited_csv(tmp_path, [_edit("url", photo_url=PID[3])]))
    assert edit["photo_id"] == PID[3]


@pytest.mark.parametrize("delimiter,encoding,newline,sep_line", [
    (";", "utf-8-sig", "\r\n", False), ("\t", "utf-8", "\n", False), (";", "utf-8", "\r\n", True),
    (",", "cp1252", "\r\n", False)])
def test_read_failed_csv_tolerates_excel_file_variants(tmp_path, delimiter, encoding, newline, sep_line):
    rows = [_edit("search", new_search_term="caf\N{LATIN SMALL LETTER E WITH ACUTE} scene")]
    (edit,), errors = mvcommon.read_failed_csv(_edited_csv(tmp_path, rows, delimiter=delimiter,
                                                           encoding=encoding, newline=newline, sep_line=sep_line))
    assert errors == [] and edit["search_term"] == "caf\N{LATIN SMALL LETTER E WITH ACUTE} scene"


def test_read_failed_csv_tolerates_hinted_reordered_headers_and_extra_columns(tmp_path):
    header = ["Note", "DECISION (url|datetime|search|not_uploaded|skip)", "Manual_ID", "Account",
              "Date (YYYY-MM-DD)", "Time (HH:MM[:SS])", "GMT (+08:00)", "My Own Column"]
    row = {"note": "n", "decision": "datetime", "manual_id": EP_WHOLE, "account": "TV", "date": "2022-02-05",
           "time": "19:49:46", "gmt": "+08:00"}
    (edit,), errors = mvcommon.read_failed_csv(_edited_csv(tmp_path, [row], header=header))
    assert errors == [] and (edit["account"], edit["taken_ms"], edit["note"]) == ("tv", S02E03_TAKEN_MS, "n")
    assert "sha256" not in edit


def test_read_failed_csv_refuses_a_foreign_csv(tmp_path):
    path = tmp_path / "other.csv"
    path.write_text("name,size\nx,1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        mvcommon.read_failed_csv(str(path))
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    with pytest.raises(ValueError):
        mvcommon.read_failed_csv(str(empty))


# ================================================================== (f) the pending list (P-11)

def test_unverified_objects_lists_pushed_but_unconfirmed_objects_with_their_age(lib, tmp_path):
    lib = copy.deepcopy(lib)
    for mid, pushed_at in ((EP_SPLIT, T(0, day=27)), (ANIME, None)):
        entry = lib[mid]
        folder = tmp_path / mid
        folder.mkdir()
        entry["folder_path"] = str(folder)
        objects = [{"sha256": o["sha256"], "role": o["kind"]} for o in mvcommon.iter_entry_objects(mid, entry)]
        push = {"objects": objects}
        if pushed_at:
            push["captured_at"] = pushed_at
        (folder / f"{entry['short_id']}.gpcapture.json").write_text(json.dumps({"pushes": [push]}),
                                                                   encoding="utf-8")
    first_chunk = lib[EP_SPLIT]["split_info"]["chunks"][0]
    first_chunk["gphotos"] = mvcommon.new_gphotos_block(first_chunk["hash"], accounts={
        "tv": {"photo_id": PID[0], "upload_ms": 5, "verified_by": "dedupkey", "confirmed": True}})
    now = datetime(2026, 9, 28, 10, 0, 0)                                  # naive = UTC
    pending = mvcommon.unverified_objects(lib, now)
    assert [(o["manual_id"], o["label"], o["pushed_at"]) for o in pending] == [
        (ANIME, "whole", None), (EP_SPLIT, "chunk 2/2", T(0, day=27))]
    assert pending[0]["age_hours"] is None and pending[1]["age_hours"] == 24.0
    aware = mvcommon.unverified_objects(lib, datetime(2026, 9, 28, 22, 0, 0, tzinfo=timezone.utc))
    assert aware[1]["age_hours"] == 36.0
    # legacy objects (no capture), confirmed ones and local ones are never pending
    assert {o["manual_id"] for o in pending} == {ANIME, EP_SPLIT}
