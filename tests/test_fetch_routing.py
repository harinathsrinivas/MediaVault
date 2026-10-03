"""IMP-C25 Step 10 — fetch plumbing: per-object account routing, the queue builder, the locator dict.

docs/feature-fetch-datetime/PLAN.md Step 10 (a, b, e, f):
  - a. cmd_fetch_route routes every uploaded OBJECT on its own (P-4: mvconfig fetch_account_overrides >
       the trusted gphotos block's learned account > the id prefix), runs one Chrome session per account,
       processes an entry whose objects span accounts in each of those sessions (its own objects only),
       pre-flights every account's profile and warns when an override contradicts a verified account.
  - b. build_fetch_queue(entry, restore_folder, ctx) keeps today's queue keys / values / order (pinned
       against a frozen copy of the old inline builder) and adds a `locator` dict per object.
  - e. IMP-A8: the dead helpers are gone; build_extras_entries forwards gphotos + labels + the title id.
  - f. parse_fetch_options / main.py forward `--manual` only when it is given.

Hermetic: libraries via `sandbox`, config via mvcommon._CONFIG_CACHE (the autouse _hermetic_mvconfig),
learned / failed state via the autouse _hermetic_gphotos_state, no browser (init_driver, the lock and the
per-entry fetch are stubs where a session is involved). Synthetic ids and keys only.
"""
import copy
import hashlib
import json
import os

import pytest

import gpweb
import mainfetch
import mvcommon

TITLE = "mov-en-2024-demo"
EPISODE = "tv-en-1999-demo-s02e05"
PID = [f"AF1QipFAKEstep10{n:04d}" for n in range(8)]   # synthetic photo ids (gpweb.MEDIA_KEY_RE shape)


def _sha(tag):
    return hashlib.sha256(tag.encode()).hexdigest()


def _sha1(tag):
    return hashlib.sha1(tag.encode()).hexdigest()


def _write_capture(folder, short_id, objects, offset_min=480):
    """A gpcapture v2 file with ONE push carrying `objects` (mvcommon.read_capture's input)."""
    os.makedirs(folder, exist_ok=True)
    doc = {"v": 2, "pushes": [{"captured_at": "2026-09-20T10:00:00Z", "device_id": "FAKE0001",
                               "device_tz": "Asia/Singapore", "device_tz_offset_min": offset_min,
                               "remote_dir": "/sdcard/Media/Series", "objects": objects}]}
    with open(os.path.join(folder, f"{short_id}.gpcapture.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


# ---------------------------------------------------------------------------
# 10.e — IMP-A8 deletions and the extras entries
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["wait_for_download", "automation_download_file", "build_download_queue"])
def test_the_dead_fetch_helpers_are_gone(name):
    # IMP-A8: empty stubs and an unreachable duplicate of the queue builder. build_fetch_queue is the one builder.
    assert not hasattr(mainfetch, name)


def _title_with_extras(tmp_path):
    whole_block = mvcommon.new_gphotos_block(_sha("bts"), uploaded_name="BTS [b1b1b1].mkv")
    chunk_block = mvcommon.new_gphotos_block(_sha("doc-1"), uploaded_name="Doc [d1d1d1].chunk.001.mkv")
    return {
        "folder_path": str(tmp_path / "Movies" / "Demo"),
        "extras": {"groups": {
            "Specials": {"items": [
                {"filename": "BTS.mkv", "sub_rel": "BTS.mkv", "short_id": "b1b1b1", "hash": _sha("bts"),
                 "uploaded": True, "status": "archived", "search_term": "BTS [b1b1b1].mkv",
                 "gphotos": whole_block},
                {"filename": "Teaser.mkv", "sub_rel": "Teaser.mkv", "short_id": "c1c1c1", "hash": _sha("teaser"),
                 "uploaded": False, "status": "local_ready", "search_term": "Teaser [c1c1c1].mkv"},
            ]},
            "Featurettes": {"items": [
                {"filename": "Doc.mkv", "sub_rel": "Deep/Doc.mkv", "short_id": "d1d1d1", "hash": _sha("doc"),
                 "uploaded": True, "status": "archived", "search_term": "Doc [d1d1d1].mkv",
                 "split_info": {"is_split": True, "total_chunks": 2, "chunks": [
                     {"filename": "Doc [d1d1d1].chunk.001.mkv", "hash": _sha("doc-1"), "gphotos": chunk_block},
                     {"filename": "Doc [d1d1d1].chunk.002.mkv", "hash": _sha("doc-2")}]}},
            ]},
        }},
    }


def test_extras_entries_keep_todays_keys_and_forward_gphotos_labels_and_the_title(tmp_path):
    title = _title_with_extras(tmp_path)
    entries = mainfetch.build_extras_entries(title, TITLE)

    folder = title["folder_path"]
    # Today's keys and values, unchanged (groups sorted, items in library order, not-uploaded skipped)...
    legacy = ("filename", "folder_path", "hash", "search_term", "short_id", "split_info")
    assert [{k: e[k] for k in legacy} for e in entries] == [
        {"filename": "Doc.mkv", "folder_path": str(tmp_path / "Movies" / "Demo" / "Featurettes" / "Deep"),
         "hash": _sha("doc"), "search_term": "Doc [d1d1d1].mkv", "short_id": "d1d1d1",
         "split_info": title["extras"]["groups"]["Featurettes"]["items"][0]["split_info"]},
        {"filename": "BTS.mkv", "folder_path": str(tmp_path / "Movies" / "Demo" / "Specials"),
         "hash": _sha("bts"), "search_term": "BTS [b1b1b1].mkv", "short_id": "b1b1b1", "split_info": None},
    ]
    # ...plus what names the objects: the item's block, its place and label, and the owning TITLE id.
    assert [(e["manual_id"], e["group_rel"], e["sub_rel"], e["label"]) for e in entries] == [
        (TITLE, "Featurettes", "Deep/Doc.mkv", "extra Featurettes/Deep/Doc.mkv"),
        (TITLE, "Specials", "BTS.mkv", "extra Specials/BTS.mkv"),
    ]
    assert entries[0]["gphotos"] is None                                   # a split item: blocks ride on its chunks
    assert entries[0]["split_info"]["chunks"][0]["gphotos"]["uploaded_name"] == "Doc [d1d1d1].chunk.001.mkv"
    assert entries[1]["gphotos"] == title["extras"]["groups"]["Specials"]["items"][0]["gphotos"]
    assert folder in entries[1]["folder_path"]


def test_extras_entries_without_a_title_id_still_build(tmp_path):
    # The pure builder stays callable without the id (test_extras.py / older callers); nothing is owned then.
    entries = mainfetch.build_extras_entries(_title_with_extras(tmp_path))
    assert [e["manual_id"] for e in entries] == [None, None]


def test_extras_labels_are_the_labels_mvcommon_gives_the_same_items(tmp_path):
    # Drift pin: learned / failed records written by the fetch must name an extra exactly as the mapper and the
    # apply engine do (mvcommon.iter_entry_objects is their single source of object labels).
    title = _title_with_extras(tmp_path)
    labels = {o["label"] for o in mvcommon.iter_entry_objects(TITLE, title) if o["kind"] == "extra_whole"}
    chunk_labels = {o["label"] for o in mvcommon.iter_entry_objects(TITLE, title) if o["kind"] == "extra_chunk"}
    for entry in mainfetch.build_extras_entries(title, TITLE):
        if entry["split_info"]:
            assert {f"{entry['label']} chunk 1/2", f"{entry['label']} chunk 2/2"} == chunk_labels
        else:
            assert entry["label"] in labels


def test_resolve_title_extras_names_the_title_of_an_episode(sandbox):
    season, episode = "tv-en-2001-demo-s01", "tv-en-2001-demo-s01e01"
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 01"
    mvcommon.save_library({
        season: {"type": "season_map", "folder_path": str(folder), "total_episodes": 1, "children": [episode],
                 "extras": {"groups": {"Specials": {"items": [
                     {"filename": "Trailer.mkv", "sub_rel": "Trailer.mkv", "short_id": "e1e1e1",
                      "hash": _sha("trailer"), "uploaded": True, "status": "archived",
                      "search_term": "Trailer [e1e1e1].mkv"}]}}}},
        episode: {"filename": "S01E01.mkv", "folder_path": str(folder), "hash": _sha("ep1"), "parent_id": season,
                  "short_id": "aaa111", "status": "archived", "uploaded": True},
    })
    entries = mainfetch.resolve_title_extras(episode)
    assert [(e["filename"], e["manual_id"], e["label"]) for e in entries] == [
        ("Trailer.mkv", season, "extra Specials/Trailer.mkv")]
    assert copy.deepcopy(entries) == entries      # plain data only (synthetic entries are never saved)


# ---------------------------------------------------------------------------
# 10.b — build_fetch_queue: today's queue, unchanged, plus a locator per object
# ---------------------------------------------------------------------------
LEGACY_KEYS = ["filename", "hash", "dest", "specific_query", "fallback_query", "fallback_index", "status"]


def _frozen_inline_queue(entry, restore_folder):
    """A FROZEN copy of fetch_single_entry's inline queue builder as it was before IMP-C25 Step 10 (HEAD b1cff7d,
    prints removed) — the parity oracle for build_fetch_queue. Do not edit to make a failing test pass."""
    queue = []
    fallback_term = entry.get("search_term")
    if not fallback_term: fallback_term = entry["filename"]
    if entry.get("split_info") and entry["split_info"].get("is_split"):
        chunks = entry["split_info"]["chunks"]
        for i, chunk in enumerate(chunks):
            fname = chunk["filename"]
            if os.path.exists(os.path.join(restore_folder, fname)):
                continue
            queue.append({"filename": fname, "hash": chunk["hash"], "dest": restore_folder,
                          "specific_query": fname, "fallback_query": fallback_term, "fallback_index": i,
                          "status": "pending"})
        for ct in entry.get("split_info", {}).get("carried_out_tracks", []):
            hname = ct.get("holder_filename")
            hhash = ct.get("holder_hash")
            if not hname or not hhash:
                continue
            if os.path.exists(os.path.join(restore_folder, hname)):
                continue
            queue.append({"filename": hname, "hash": hhash, "dest": restore_folder, "specific_query": hname,
                          "fallback_query": fallback_term, "fallback_index": 0, "status": "pending"})
    else:
        fname = entry["filename"]
        if not os.path.exists(os.path.join(restore_folder, fname)):
            queue.append({"filename": fname, "hash": entry["hash"], "dest": restore_folder, "specific_query": fname,
                          "fallback_query": fallback_term, "fallback_index": 0, "status": "pending"})
    return queue


def _split(stem, sid, n, holders=()):
    info = {"is_split": True, "total_chunks": n,
            "chunks": [{"filename": f"{stem} [{sid}].chunk.{i:03d}.mkv", "hash": _sha(f"{stem}#{i}")}
                       for i in range(1, n + 1)]}
    if holders:
        info["carried_out_tracks"] = list(holders)
    return info


def _shapes(tmp_path):
    """(name, entry, files already in restore/) — every shape the old builder handled, incl. its odd corners."""
    folder = str(tmp_path / "title")
    h1 = {"holder_filename": "Film [f1f1f1].holder.mkv", "holder_hash": _sha("holder-1")}
    h2 = {"holder_filename": "Film [f1f1f1].holder2.mkv"}                       # no hash -> skipped (as before)
    h3 = {"holder_hash": _sha("holder-3")}                                       # no name -> skipped (as before)
    h4 = {"holder_filename": "Film [f1f1f1].holder4.mkv", "holder_hash": _sha("holder-4")}
    return [
        ("whole", {"filename": "Film.mkv", "short_id": "f1f1f1", "folder_path": folder, "hash": _sha("film"),
                   "search_term": "Film [f1f1f1].mkv"}, []),
        ("whole, no search_term", {"filename": "Film.mkv", "folder_path": folder, "hash": _sha("film")}, []),
        ("whole, empty search_term", {"filename": "Film.mkv", "folder_path": folder, "hash": _sha("film"),
                                      "search_term": ""}, []),
        ("whole, already staged", {"filename": "Film.mkv", "folder_path": folder, "hash": _sha("film"),
                                   "search_term": "Film [f1f1f1].mkv"}, ["Film.mkv"]),
        ("split, one staged", {"filename": "Film.mkv", "short_id": "f1f1f1", "folder_path": folder,
                               "hash": "unused", "search_term": "Film [f1f1f1].mkv",
                               "split_info": _split("Film", "f1f1f1", 3)}, ["Film [f1f1f1].chunk.002.mkv"]),
        ("split + holders", {"filename": "Film.mkv", "short_id": "f1f1f1", "folder_path": folder, "hash": "x",
                             "search_term": "Film [f1f1f1].mkv",
                             "split_info": _split("Film", "f1f1f1", 2, (h1, h2, h3, h4))},
         ["Film [f1f1f1].holder4.mkv"]),
        ("split_info not split", {"filename": "Film.mkv", "folder_path": folder, "hash": _sha("film"),
                                  "search_term": "Film [f1f1f1].mkv",
                                  "split_info": {"is_split": False, "chunks": []}}, []),
        ("unicode name", {"filename": "Fïlm  Ünï.mkv", "short_id": "f2f2f2", "folder_path": folder,
                          "hash": _sha("unicode"), "search_term": "Fïlm  Ünï [f2f2f2].mkv"}, []),
    ]


@pytest.mark.parametrize("index", range(8))
def test_queue_keeps_todays_keys_values_and_order(tmp_path, index):
    name, entry, staged = _shapes(tmp_path)[index]
    restore = tmp_path / "title" / "restore"
    restore.mkdir(parents=True)
    for f in staged:
        (restore / f).write_bytes(b"staged")

    queue = mainfetch.build_fetch_queue(entry, str(restore), entry_id="mov-en-2020-film")

    assert [{k: item[k] for k in LEGACY_KEYS} for item in queue] == _frozen_inline_queue(entry, str(restore)), name
    assert all(list(item) == LEGACY_KEYS + ["locator"] for item in queue), name    # today's keys first, in order


def test_queue_of_extras_entries_matches_the_frozen_builder(tmp_path):
    title = _title_with_extras(tmp_path)
    for entry in mainfetch.build_extras_entries(title, TITLE):
        restore = os.path.join(entry["folder_path"], "restore")
        os.makedirs(restore, exist_ok=True)
        queue = mainfetch.build_fetch_queue(entry, restore)
        assert [{k: item[k] for k in LEGACY_KEYS} for item in queue] == _frozen_inline_queue(entry, restore)


def _learned_block(sha256, sha1):
    """A trusted block that learned the object lives in the MOVIES account (an X-Files-like wrong-account item)."""
    return mvcommon.new_gphotos_block(
        sha256, sha1=sha1, date_utc="2022-02-05T11:49:46Z", uploaded_name="Demo.S02E05 [abc123].mkv",
        pushed={"at": "2026-09-20T10:00:00Z", "device_id": "FAKE0001", "device_tz_offset_min": 480},
        accounts={"movies": {"photo_id": PID[0], "taken_ms": 1644061786000, "tz_offset_ms": 28800000,
                             "upload_ms": 1700000000000, "dedup_key": gpweb.dedup_key(sha1),
                             "verified_at": "2026-09-25T10:00:00Z", "verified_by": "dedupkey", "source": "map",
                             "approach": {"best": "photo_id", "ok": ["photo_id", "exact_name"],
                                          "at": "2026-09-25T10:00:00Z"},
                             "alternates": [PID[1]], "confirmed": True}},
        home_account="movies", search_override={"query": "demo 205 custom", "at": "2026-09-26T10:00:00Z"})


def _episode(tmp_path, block=None, sha256=None):
    sha256 = sha256 or _sha("demo-ep")
    entry = {"filename": "Demo.S02E05.mkv", "short_id": "abc123", "folder_path": str(tmp_path / "Season 02"),
             "hash": sha256, "search_term": "Demo.S02E05 [abc123].mkv", "status": "archived", "uploaded": True}
    if block is not None:
        entry["gphotos"] = block
    return entry


def test_locator_from_a_trusted_block_routes_to_its_learned_account(tmp_path):
    sha1 = _sha1("demo episode bytes")
    entry = _episode(tmp_path, _learned_block(_sha("demo-ep"), sha1))

    [item] = mainfetch.build_fetch_queue(entry, str(tmp_path / "restore"), entry_id=EPISODE)

    assert item["locator"] == {
        "manual_id": EPISODE, "kind": "whole", "index": None, "label": "whole", "sha256": _sha("demo-ep"),
        "category": "tv", "short_id": "abc123", "filename": "Demo.S02E05.mkv",
        "uploaded_name": "Demo.S02E05 [abc123].mkv", "cloud_state": "confirmed",
        "sha1": sha1, "dedup_key": gpweb.dedup_key(sha1), "hash_b64": gpweb.sha1_hex_to_std_b64(sha1),
        "date_utc": "2022-02-05T11:49:46Z", "taken_ms": 1644061786000, "tz_offset_ms": 28800000,
        "device_tz_offset_min": 480,
        "account": "movies", "route": {"account": "movies", "reason": "learned"},
        "photo_id": PID[0], "alternates": [PID[1]], "approach_best": "photo_id",
        "search_override": "demo 205 custom",
        "queries": {"s2": ["Demo.S02E05 [abc123].mkv"], "s3": ["Demo abc123"], "s3u": ["demo 205 custom"],
                    "s4": [["Demo.S02E05.mkv", 0], ["Demo.S02E05 [abc123].mkv", 0]]},
    }


def test_an_override_beats_the_learned_account_and_has_no_record_there(tmp_path, monkeypatch):
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"fetch_account_overrides": {"tv-en-1999-demo": "anime"}})
    entry = _episode(tmp_path, _learned_block(_sha("demo-ep"), _sha1("bytes")))

    [item] = mainfetch.build_fetch_queue(entry, str(tmp_path / "restore"), entry_id=EPISODE)

    loc = item["locator"]
    assert loc["account"] == "anime" and loc["route"] == {"account": "anime", "reason": "override"}
    assert (loc["photo_id"], loc["alternates"], loc["approach_best"], loc["tz_offset_ms"]) == (None, [], None, None)
    assert loc["taken_ms"] == 1644061786000                   # from DateUTC: the item's taken second is not per account
    assert loc["sha1"] == _sha1("bytes")                       # the content key is the object's, whatever the account


def test_an_untrusted_block_is_ignored_and_the_capture_speaks(tmp_path):
    # P-2: a block pinned to OTHER bytes (e.g. before a re-master) is never read; the capture of these bytes is.
    stale = _learned_block(_sha("old bytes"), _sha1("old bytes"))
    entry = _episode(tmp_path, stale)
    sha1 = _sha1("new bytes")
    _write_capture(entry["folder_path"], "abc123", [{
        "sha256": entry["hash"], "role": "whole", "uploaded_name": "Demo.S02E05 [abc123].mkv", "sha1": sha1,
        "date_utc": "2024-05-03T23:18:02Z", "approach_hint": "hash"}])

    [item] = mainfetch.build_fetch_queue(entry, str(tmp_path / "restore"), entry_id=EPISODE)

    loc = item["locator"]
    assert loc["route"] == {"account": "tv", "reason": "prefix"} and loc["account"] == "tv"
    assert (loc["sha1"], loc["dedup_key"], loc["date_utc"]) == (sha1, gpweb.dedup_key(sha1), "2024-05-03T23:18:02Z")
    assert loc["taken_ms"] == 1714778282000                    # PLAN C1c reference: DateUTC = the taken second
    assert (loc["device_tz_offset_min"], loc["approach_best"], loc["cloud_state"]) == (480, "hash", "unverified")
    assert (loc["photo_id"], loc["search_override"], loc["queries"]["s3u"]) == (None, None, [])


def test_the_session_account_is_the_locator_account(tmp_path):
    ctx = mainfetch.FetchContext()
    ctx.start_session("anime")
    [item] = mainfetch.build_fetch_queue(_episode(tmp_path), str(tmp_path / "restore"), ctx, entry_id=EPISODE)
    assert item["locator"]["account"] == "anime"                       # where this session searches
    assert item["locator"]["route"] == {"account": "tv", "reason": "prefix"}   # what P-4 answered


def test_chunks_and_holders_are_named_like_mvcommon_names_them(tmp_path):
    holder = {"holder_filename": "Film [f1f1f1].holder.mkv", "holder_hash": _sha("holder")}
    entry = {"filename": "Film.2019.2160p.mkv", "short_id": "f1f1f1", "folder_path": str(tmp_path / "Film"),
             "hash": _sha("film"), "search_term": "Film.2019.2160p [f1f1f1].mkv", "status": "archived",
             "uploaded": True, "split_info": _split("Film", "f1f1f1", 3, (holder,))}
    entry["split_info"]["chunks"][1]["gphotos"] = mvcommon.new_gphotos_block(
        entry["split_info"]["chunks"][1]["hash"],
        accounts={"movies": {"photo_id": PID[2], "verified_by": "filename", "source": "map",
                             "verified_at": "2026-09-25T10:00:00Z"}})

    queue = mainfetch.build_fetch_queue(entry, str(tmp_path / "Film" / "restore"), entry_id="mov-en-2019-film")

    assert [(i["locator"]["kind"], i["locator"]["index"], i["locator"]["label"]) for i in queue] == [
        ("chunk", 1, "chunk 1/3"), ("chunk", 2, "chunk 2/3"), ("chunk", 3, "chunk 3/3"), ("holder", 1, "holder")]
    assert [i["locator"]["photo_id"] for i in queue] == [None, PID[2], None, None]
    assert [i["locator"]["uploaded_name"] for i in queue] == [i["filename"] for i in queue]   # parts keep their names
    assert queue[0]["locator"]["queries"]["s3"] == ["Film f1f1f1"]
    assert queue[1]["locator"]["queries"]["s4"] == [["Film [f1f1f1].chunk.002.mkv", 0],
                                                     ["Film.2019.2160p [f1f1f1].mkv", 1]]
    assert {i["locator"]["manual_id"] for i in queue} == {"mov-en-2019-film"}


def test_extras_objects_carry_the_title_and_its_extras_labels(tmp_path):
    entries = mainfetch.build_extras_entries(_title_with_extras(tmp_path), TITLE)
    doc, bts = entries
    doc_queue = mainfetch.build_fetch_queue(doc, os.path.join(doc["folder_path"], "restore"))
    bts_queue = mainfetch.build_fetch_queue(bts, os.path.join(bts["folder_path"], "restore"))

    assert [(i["locator"]["manual_id"], i["locator"]["kind"], i["locator"]["label"]) for i in doc_queue + bts_queue] == [
        (TITLE, "extra_chunk", "extra Featurettes/Deep/Doc.mkv chunk 1/2"),
        (TITLE, "extra_chunk", "extra Featurettes/Deep/Doc.mkv chunk 2/2"),
        (TITLE, "extra_whole", "extra Specials/BTS.mkv")]
    assert [i["locator"]["category"] for i in doc_queue + bts_queue] == ["movies"] * 3
    assert bts_queue[0]["locator"]["uploaded_name"] == "BTS [b1b1b1].mkv"            # from the forwarded block
    assert doc_queue[0]["locator"]["uploaded_name"] == "Doc [d1d1d1].chunk.001.mkv"   # from the chunk's block


@pytest.mark.parametrize("manual_id, category", [
    ("mov-en-2020-film", "movies"), ("tv-en-2001-demo-s01e01", "tv"),
    ("ani-ja-2006-demo07", "anime"), ("oth-en-2024-final-e01", "others")])
def test_every_category_is_located_in_its_prefix_account_without_learned_data(tmp_path, manual_id, category):
    entry = {"filename": "Some.File.mkv", "short_id": "a1a1a1", "folder_path": str(tmp_path), "hash": _sha(manual_id),
             "search_term": "Some.File [a1a1a1].mkv"}
    [item] = mainfetch.build_fetch_queue(entry, str(tmp_path / "restore"), entry_id=manual_id)
    loc = item["locator"]
    assert (loc["category"], loc["account"], loc["route"]["reason"]) == (category, category, "prefix")
    assert loc["uploaded_name"] == "Some.File [a1a1a1].mkv"
    assert (loc["sha1"], loc["photo_id"], loc["taken_ms"], loc["cloud_state"]) == (None, None, None, "local")


def test_an_object_the_library_cannot_name_still_gets_a_minimal_locator(tmp_path):
    # A malformed hash (no 64-hex pin) matches no mvcommon object; the item is queued exactly as before.
    entry = {"filename": "x.mkv", "folder_path": str(tmp_path), "hash": "deadbeef", "search_term": "x"}
    [item] = mainfetch.build_fetch_queue(entry, str(tmp_path / "restore"))
    loc = item["locator"]
    assert (loc["manual_id"], loc["kind"], loc["label"], loc["sha256"]) == (None, "whole", "whole", "deadbeef")
    assert loc["uploaded_name"] is None and loc["queries"]["s2"] == [] and loc["queries"]["s3"] == []
    assert loc["route"] == {"account": "movies", "reason": "prefix"}   # DEFAULT_PROFILE, like profile_for_id('')


def test_building_the_queue_never_writes_the_library(sandbox, tmp_path):
    mvcommon.save_library({EPISODE: _episode(tmp_path, _learned_block(_sha("demo-ep"), _sha1("b")))})
    before = {p: p.read_bytes() for p in sandbox["lib_series"].parent.iterdir()}
    entry = mvcommon.load_library()[EPISODE]
    mainfetch.build_fetch_queue(entry, str(tmp_path / "restore"), entry_id=EPISODE)
    assert {p: p.read_bytes() for p in sandbox["lib_series"].parent.iterdir()} == before


# ---------------------------------------------------------------------------
# 10.a — cmd_fetch_route routes every OBJECT (P-4) and runs one Chrome session per account
# ---------------------------------------------------------------------------
SEASON = "tv-en-2001-demo-s01"
EP1, EP2 = SEASON + "e01", SEASON + "e02"
MOVIE = "mov-en-2020-film"
WEB_MARKERS = ("🔹 PROCESSING:", "Detected Split File", "✅ MOVED:", "✅ ENTRY COMPLETE.")   # PLAN P-13


def _verified_in(account, sha256, proof="dedupkey", pid=PID[3]):
    """A trusted block whose `account` holds a copy proven by `proof` (what the apply engine writes)."""
    return mvcommon.new_gphotos_block(sha256, accounts={account: {
        "photo_id": pid, "verified_at": "2026-09-25T10:00:00Z", "verified_by": proof, "source": "map"}},
        home_account=account)


def _seed(sandbox, ep1_block=None, extra_block=None):
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    library = {SEASON: {"type": "season_map", "folder_path": str(folder), "total_episodes": 2,
                        "children": [EP1, EP2]}}
    for n, eid in enumerate((EP1, EP2), start=1):
        library[eid] = {"filename": f"S01E0{n}.mkv", "folder_path": str(folder), "hash": _sha(eid),
                        "parent_id": SEASON, "status": "archived", "uploaded": True,
                        "short_id": mvcommon.generate_short_id(eid), "search_term": f"S01E0{n}.mkv"}
    if ep1_block is not None:
        library[EP1]["gphotos"] = ep1_block
    if extra_block is not None:
        library[SEASON]["extras"] = {"groups": {"Specials": {"items": [{
            "filename": "Trailer.mkv", "sub_rel": "Trailer.mkv", "hash": _sha("trailer"), "uploaded": True,
            "status": "archived", "short_id": "e1e1e1", "search_term": "Trailer [e1e1e1].mkv",
            "gphotos": extra_block}]}}}
    mvcommon.save_library(library)
    return folder


class _Driver:
    """A truthy stand-in for one account's attached Chrome."""

    def __init__(self, profile, events):
        self.profile, self._events = profile, events

    @property
    def window_handles(self):
        return []

    def quit(self):
        self._events.append(("quit", self.profile))


@pytest.fixture()
def route(monkeypatch):
    """Stub cmd_fetch_route's side effects (browser launch, lock, port probe); `route.fetch_stub()` also stubs
    fetch_single_entry, recording (session account, entry id or label, whether the entry is split across accounts)."""
    import types
    import contextlib
    st = types.SimpleNamespace(events=[], fetched=[])

    def _init(profile_key="movies"):
        st.events.append(("init", profile_key))
        return _Driver(profile_key, st.events)

    def fetch_stub():
        def _fetch(driver, entry, temp_dir=None, entry_id=None, ctx=None):
            st.fetched.append((driver.profile, entry_id or entry.get("label"),
                               ctx.plan_for(entry, entry_id) is not None))
        monkeypatch.setattr(mainfetch, "fetch_single_entry", _fetch)

    st.fetch_stub = fetch_stub
    monkeypatch.setattr(mainfetch, "init_driver", _init)
    monkeypatch.setattr(mainfetch, "fetch_session_lock", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(mainfetch, "_debug_port_free", lambda *a, **k: True)
    return st


def _inits(st):
    return [e[1] for e in st.events if e[0] == "init"]


def test_a_learned_account_moves_an_episode_without_any_override(sandbox, route, capsys):
    _seed(sandbox, ep1_block=_verified_in("movies", _sha(EP1)))
    route.fetch_stub()

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert _inits(route) == ["tv", "movies"]
    assert route.fetched == [("tv", EP2, False), ("movies", EP1, False)]
    assert "   > [Account] Switching to profile 'movies' for 1 item(s) (learned account)" in out
    assert out.rstrip().endswith("✅ Batch Processing Complete.")


def test_a_single_episode_goes_straight_to_its_learned_account(sandbox, route, capsys):
    # The X-Files case once a fetch or the mapper has learned it: no override needed, no empty tv session.
    _seed(sandbox, ep1_block=_verified_in("movies", _sha(EP1)))
    route.fetch_stub()

    mainfetch.cmd_fetch_route(EP1)

    out = capsys.readouterr().out
    assert _inits(route) == ["movies"]
    assert r"   > [Account] Profile for tv-en-2001-demo-s01e01: 'tv' (C:\Media\Utils\ChromeProfile_TV)" in out
    assert "   > [Account] Switching to profile 'movies' for 1 item(s) (learned account)" in out


@pytest.mark.parametrize("override, proof, warned", [
    ("anime", "dedupkey", True),      # contradicts a content-proven copy in movies
    ("anime", "filename", True),      # ... or a name-proven one
    ("movies", "dedupkey", False),    # agrees
    ("anime", "manual", False),       # a user-vouched copy is not verified yet (P-5): nothing to contradict
])
def test_an_override_wins_and_warns_only_against_a_verified_account(sandbox, route, monkeypatch, capsys,
                                                                     override, proof, warned):
    _seed(sandbox, ep1_block=_verified_in("movies", _sha(EP1), proof=proof))
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"fetch_account_overrides": {EP1: override}})
    route.fetch_stub()

    mainfetch.cmd_fetch_route(EP1)

    out = capsys.readouterr().out
    assert _inits(route) == [override]                              # P-4: the override wins
    warning = (f"   > ⚠️ override '{override}' for {EP1} differs from the verified account 'movies' (1 file(s)) "
               f"— the override wins.")
    assert out.count(warning) == (1 if warned else 0)
    assert "differs from the verified account" in out if warned else "differs" not in out
    assert not any(marker in warning for marker in WEB_MARKERS)


def test_an_untrusted_block_never_routes(sandbox, route, capsys):
    # P-2: a block pinned to other bytes (from before a re-master) says nothing about these bytes' account.
    _seed(sandbox, ep1_block=_verified_in("movies", _sha("bytes from before a re-master")))
    route.fetch_stub()

    mainfetch.cmd_fetch_route(SEASON)

    assert _inits(route) == ["tv"] and "Switching" not in capsys.readouterr().out
    assert route.fetched == [("tv", EP1, False), ("tv", EP2, False)]


def test_with_no_override_and_nothing_learned_the_groups_are_todays_single_session(sandbox):
    _seed(sandbox)
    targets = mainfetch.resolve_targets(SEASON)
    ids = mainfetch.resolve_target_ids(SEASON)
    ctx = mainfetch.FetchContext()
    groups = mainfetch._account_groups(SEASON, "tv", targets, ids, [], ctx)
    assert groups == [("tv", list(zip(targets, ids)), [])]
    assert (ctx.plans, ctx.reasons) == ({}, {})


def test_extras_follow_their_own_learned_account(sandbox, route, capsys):
    _seed(sandbox, extra_block=_verified_in("movies", _sha("trailer")))
    route.fetch_stub()

    mainfetch.cmd_fetch_route(SEASON, fetch_extras=True)

    out = capsys.readouterr().out
    assert _inits(route) == ["tv", "movies"]
    assert route.fetched == [("tv", EP1, False), ("tv", EP2, False), ("movies", "extra Specials/Trailer.mkv", False)]
    assert "   > [Account] Switching to profile 'movies' for 1 item(s) (learned account)" in out
    lines = out.splitlines()
    assert lines.index("=== 📎 FETCHING 1 EXTRA(S) for tv-en-2001-demo-s01 ===") > lines.index(
        "   > [Account] Switching to profile 'movies' for 1 item(s) (learned account)")


def test_a_missing_profile_stops_the_batch_before_its_chrome_starts(sandbox, route, monkeypatch, capsys, tmp_path):
    _seed(sandbox)
    missing = tmp_path / "profiles" / "ChromeProfile"                # never created
    monkeypatch.setattr(mainfetch, "CHROME_PROFILES", {**mainfetch.CHROME_PROFILES, "movies": str(missing)})
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"fetch_account_overrides": {EP2: "movies"}})
    route.fetch_stub()

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert _inits(route) == ["tv"]                                   # movies' Chrome never starts
    assert route.fetched == [("tv", EP1, False)]
    assert f"❌ The Chrome profile for 'movies' does not exist: {missing} - create it" in out
    assert "Batch Processing Complete" not in out                    # aborted, like a logged-out profile


def test_a_missing_profile_for_the_only_account_launches_nothing(route, monkeypatch, capsys, tmp_path):
    missing = tmp_path / "profiles" / "ChromeProfile_Others"
    monkeypatch.setattr(mainfetch, "CHROME_PROFILES", {**mainfetch.CHROME_PROFILES, "others": str(missing)})
    monkeypatch.setattr(mainfetch, "resolve_targets", lambda *a, **k: [
        {"filename": "Final.mkv", "folder_path": str(tmp_path), "hash": _sha("final")}])
    monkeypatch.setattr(mainfetch, "resolve_target_ids", lambda *a, **k: ["oth-en-2024-final-e01"])
    route.fetch_stub()

    mainfetch.cmd_fetch_route("oth-en-2024-final-e01")

    assert _inits(route) == [] and route.fetched == []
    assert "❌ The Chrome profile for 'others' does not exist" in capsys.readouterr().out


def test_the_suite_never_depends_on_this_machines_profile_folders(monkeypatch, tmp_path):
    # tests/conftest.py _hermetic_chrome_profiles: the real default paths read as present on any machine (the
    # Others profile does not exist here); a path a test chose gets the real check; unknown accounts always do.
    assert mainfetch.profile_preflight("others") is None
    assert mainfetch.profile_preflight("sports").startswith("unknown Google account 'sports'")
    monkeypatch.setattr(mainfetch, "CHROME_PROFILES", {**mainfetch.CHROME_PROFILES, "others": str(tmp_path / "x")})
    assert "does not exist" in mainfetch.profile_preflight("others")


# --- an entry whose objects live in two accounts: fetched in both sessions, each taking its own objects ------
@pytest.fixture()
def world(tmp_path, monkeypatch):
    """The C29 harvester fakes (tests/test_fetch_harvester.py): a tmp Downloads folder, a fake clock in mainfetch,
    and scripted Chrome downloads."""
    import types
    from test_fetch_harvester import _Chrome, _Clock
    folder = tmp_path / "Downloads"
    folder.mkdir()
    clock = _Clock()
    monkeypatch.setattr(mainfetch, "SYSTEM_DOWNLOADS_FOLDER", str(folder))
    monkeypatch.setattr(mainfetch, "time", clock)
    assert str(tmp_path) in mainfetch.SYSTEM_DOWNLOADS_FOLDER     # never the real Downloads folder
    return types.SimpleNamespace(clock=clock, chrome=_Chrome(folder, clock), downloads=folder)


CHUNK_BYTES = [bytes([0x41 + n]) * 4096 for n in range(3)]


def _seed_split_movie(sandbox, tv_chunk=3):
    """A 3-chunk movie whose chunk `tv_chunk` learned (verified) that it lives in the TV account."""
    folder = sandbox["local_root"] / "Movies" / "Film (2020)"
    folder.mkdir(parents=True, exist_ok=True)
    chunks = [{"filename": f"Film [f1f1f1].chunk.00{n}.mkv", "hash": hashlib.sha256(CHUNK_BYTES[n - 1]).hexdigest()}
              for n in (1, 2, 3)]
    chunks[tv_chunk - 1]["gphotos"] = _verified_in("tv", chunks[tv_chunk - 1]["hash"], proof="filename")
    mvcommon.save_library({MOVIE: {"filename": "Film.mkv", "short_id": "f1f1f1", "folder_path": str(folder),
                                   "hash": _sha("film"), "search_term": "Film [f1f1f1].mkv", "status": "archived",
                                   "uploaded": True, "split_info": {"is_split": True, "total_chunks": 3,
                                                                    "chunks": chunks}}})
    return folder, [c["filename"] for c in chunks]


def _trigger_per_session(monkeypatch, world, payload_for):
    """trigger_download stand-in recording (session account, query); payload_for(query, n) -> bytes, None (the
    transfer breaks) or False (nothing found), n = how often that query was sent before."""
    sent = []

    def _trigger(driver, query, index=0):
        n = sum(1 for _profile, q in sent if q == query)
        sent.append((driver.profile, query))
        payload = payload_for(query, n)
        if payload is False:
            return False
        return world.chrome.start(query, payload, 30)

    monkeypatch.setattr(mainfetch, "trigger_download", _trigger)
    return sent


def test_an_entry_split_across_accounts_is_fetched_in_both_sessions(sandbox, route, world, monkeypatch, capsys):
    folder, names = _seed_split_movie(sandbox)
    payloads = dict(zip(names, CHUNK_BYTES))
    sent = _trigger_per_session(monkeypatch, world, lambda q, n: payloads.get(q, False))

    mainfetch.cmd_fetch_route(MOVIE)

    out = capsys.readouterr().out
    assert _inits(route) == ["movies", "tv"]
    assert sent == [("movies", names[0]), ("movies", names[1]), ("tv", names[2])]   # each chunk in its account
    lines = out.splitlines()
    expected_order = [
        "🔹 PROCESSING: Film.mkv (f1f1f1)",
        "   > Detected Split File (3 chunks)",
        "   > [Account] 1 of its 3 file(s) live in another account — fetched in the 'tv' session.",
        "   === ATTEMPT 1 (2 files) ===",
        "   ⏸️ 1 file(s) left for the 'tv' session.",
        "   > [Account] Switching to profile 'tv' for 1 item(s) (learned account)",
        "🔸 CONTINUING: Film.mkv (f1f1f1) — its file(s) in the 'tv' account",
        "   === ATTEMPT 1 (1 files) ===",
        "   ✅ ENTRY COMPLETE.",
        "✅ Batch Processing Complete.",
    ]
    positions = [lines.index(line) for line in expected_order]
    assert positions == sorted(positions)
    assert out.count("🔹 PROCESSING:") == 1 and out.count("Detected Split File") == 1     # counted once by the web UI
    assert out.count("✅ ENTRY COMPLETE.") == 1 and out.count("✅ MOVED:") == 3
    web = pytest.importorskip("webui.server")
    assert web._parse_progress(out) == {"done": 3, "total": 3}
    for name, data in zip(names, CHUNK_BYTES):
        assert (folder / "restore" / name).read_bytes() == data
    assert sorted(r["label"] for r in mvcommon.read_learned("movies")) == ["chunk 1/3", "chunk 2/3"]
    assert [r["label"] for r in mvcommon.read_learned("tv")] == ["chunk 3/3"]    # learned in its own account


def test_the_last_session_judges_the_whole_entry(sandbox, route, world, monkeypatch, capsys):
    # chunk 1 (movies) is lost twice; the tv session still fetches chunk 3 — and the entry ends INCOMPLETE once.
    folder, names = _seed_split_movie(sandbox)
    payloads = dict(zip(names, CHUNK_BYTES))
    _trigger_per_session(monkeypatch, world, lambda q, n: None if q == names[0] else payloads.get(q, False))

    mainfetch.cmd_fetch_route(MOVIE)

    out = capsys.readouterr().out
    assert out.count("❌ ENTRY INCOMPLETE.") == 1 and "✅ ENTRY COMPLETE." not in out
    assert out.index("⏸️ 1 file(s) left for the 'tv' session.") < out.index("❌ ENTRY INCOMPLETE.")
    assert (folder / "restore" / names[2]).read_bytes() == CHUNK_BYTES[2]
    [row] = mvcommon.read_failed()
    assert (row["account"], row["label"], row["reason"]) == ("movies", "chunk 1/3", "download-lost")


def test_a_session_whose_objects_are_all_staged_says_so_and_hands_over(sandbox, route, world, monkeypatch, capsys):
    folder, names = _seed_split_movie(sandbox)
    (folder / "restore").mkdir()
    for name, data in zip(names[:2], CHUNK_BYTES):
        (folder / "restore" / name).write_bytes(data)                   # the movies chunks are already here
    sent = _trigger_per_session(monkeypatch, world, lambda q, n: CHUNK_BYTES[2] if q == names[2] else False)

    mainfetch.cmd_fetch_route(MOVIE)

    out = capsys.readouterr().out
    assert sent == [("tv", names[2])]
    assert "   ✅ Nothing left to fetch in this account." in out and "All files already exist" not in out
    assert out.count("✅ ENTRY COMPLETE.") == 1
    for new in ("   ✅ Nothing left to fetch in this account.", "   ⏸️ 1 file(s) left for the 'tv' session.",
                "🔸 CONTINUING: Film.mkv (f1f1f1) — its file(s) in the 'tv' account",
                "   > [Account] 1 of its 3 file(s) live in another account — fetched in the 'tv' session."):
        assert not any(marker in new for marker in WEB_MARKERS)          # P-13: no new line carries a web marker


# ---------------------------------------------------------------------------
# 10.f — --manual: parse_fetch_options, and main.py forwards it only when given
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("argv, manual", [
    (["mainfetch.py", "fetch", "tv-x"], False),
    (["mainfetch.py", "fetch", "tv-x", "--manual"], True),
    (["mainfetch.py", "fetch", "tv-x", "episodes", "1-3", "--manual", "tempdir", "D:\\t"], True),
    (["mainfetch.py", "fetch", "--manual"], False),                    # the id slot is never an option
    (["mainfetch.py", "fetch", "tv-x", "--manual-ish"], False),
])
def test_parse_fetch_options_reads_manual_anywhere_after_the_id(argv, manual):
    assert mainfetch.parse_fetch_options(argv) == {"manual": manual}


def test_parse_fetch_args_keeps_its_four_tuple_with_manual_present():
    assert mainfetch.parse_fetch_args(["mainfetch.py", "fetch", "tv-x", "--manual", "episodes", "1-3"]) == (
        "tv-x", "1-3", False, None)


@pytest.mark.parametrize("extra, manual", [([], False), (["--manual"], True)])
def test_the_mainfetch_command_line_carries_manual_into_the_run(monkeypatch, extra, manual):
    seen = []
    monkeypatch.setattr(mainfetch, "cmd_fetch_route", lambda *a, **k: seen.append((a, k)))
    mainfetch.cli(["mainfetch.py", "fetch", "tv-x", "episodes", "1-3", "--fetchExtras", "tempdir", "D:\\t"] + extra)
    assert seen == [(("tv-x", "1-3", True), {"temp_dir": "D:\\t", "manual": manual})]


def test_one_run_context_carries_manual_to_every_entry(sandbox, route, monkeypatch):
    _seed(sandbox)
    contexts = []
    monkeypatch.setattr(mainfetch, "fetch_single_entry",
                        lambda driver, entry, temp_dir=None, entry_id=None, ctx=None: contexts.append(ctx))
    mainfetch.cmd_fetch_route(SEASON, manual=True)
    assert len(contexts) == 2 and contexts[0] is contexts[1] and contexts[0].manual is True


def test_the_fetch_child_gets_manual_only_when_given(monkeypatch):
    import main
    spawned = []

    class _Child:
        stdout = []

        def wait(self):
            return 0

    monkeypatch.setattr(main.subprocess, "Popen", lambda cmd, **k: spawned.append(list(cmd)) or _Child())
    monkeypatch.setattr(main, "merge_learned_gphotos", lambda: None)
    main.cmd_dispatch_fetch("tv-x", "1-3", fetch_extras=True, temp_dir="D:\\t")
    main.cmd_dispatch_fetch("tv-x", "1-3", fetch_extras=True, temp_dir="D:\\t", manual=True)
    base = ["python", "mainfetch.py", "fetch", "tv-x", "episodes", "1-3", "--fetchExtras", "tempdir", "D:\\t"]
    assert spawned == [base, base + ["--manual"]]                       # without it: byte-for-byte today's argv


@pytest.mark.parametrize("command, target", [("fetch", "cmd_dispatch_fetch"), ("fetch_restore", "cmd_fetch_restore")])
def test_the_main_command_line_forwards_manual_only_when_given(command, target):
    # main.py's own dispatcher block, driven with recorders (tests/test_prep_push_rep_enrich_cli.py's harness).
    from test_prep_push_rep_enrich_cli import parse_argv
    assert parse_argv(["main.py", command, "tv-x", "episodes", "1-3"]) == (target, {
        "manual_id": "tv-x", "episode_range": "1-3", "fetch_extras": False, "temp_dir": None, "manual": False})
    assert parse_argv(["main.py", command, "tv-x", "--manual", "tempdir", "D:\\t"]) == (target, {
        "manual_id": "tv-x", "episode_range": None, "fetch_extras": False, "temp_dir": "D:\\t", "manual": True})


def test_the_web_ui_never_passes_manual():
    import inspect
    web = pytest.importorskip("webui.server")
    source = inspect.getsource(web._run_fetch_restore)
    assert "manual" not in source and "cmd_fetch_restore(" in source
