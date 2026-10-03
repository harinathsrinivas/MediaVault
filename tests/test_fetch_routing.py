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
