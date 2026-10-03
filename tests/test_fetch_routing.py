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

import pytest

import mainfetch
import mvcommon

TITLE = "mov-en-2024-demo"


def _sha(tag):
    return hashlib.sha256(tag.encode()).hexdigest()


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
