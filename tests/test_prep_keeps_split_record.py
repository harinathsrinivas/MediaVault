"""IMP-C33 — re-prepping an unchanged file keeps its split record, and a whole-file
push drops a stale one.

An interrupted split push leaves the entry `local_ready` with its split recorded in
`split_info` and the remaining chunks in `_parts/`. Re-running an autopilot re-preps
the entry first. cmd_prep rebuilt it from scratch, which dropped the split record,
so since IMP-C32 the push refused the leftover chunks as unrecorded (before IMP-C32
it uploaded them and archived the entry with no split record).

The rule (user ruling 2026-10-02): when cmd_prep re-preps a `local_ready`, not yet
uploaded entry and the file's hash is unchanged, it keeps the entry's `split_info`
(and its `re_hashed` flag), exactly as the extras merge already does for an
unchanged extra. A changed file still drops the record: its old chunks are stale,
stay where they are, and the next push refuses them by name.

Keeping the record makes one more state reachable: an entry that still records an
unfinished split while the next push uploads the file WHOLE (the leftovers were
deleted, or no split size was given). A first archive that completes as a
whole-file push therefore drops the record, so `split_info` describes what was
uploaded. An entry that was already uploaded keeps its record, as before.

Fixtures (docs/testing-strategy.md §4): `sandbox`, `mock_device`, `make_video`,
`stub_tech_specs`, `fake_dummy`. Device lookups index by `.name` (§8.1). "Restorable"
is proved the way a user would: the objects the entry records are copied from the
fake device into `restore/` (what `fetch` does) and the real `cmd_restore` runs.
"""
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import types

import pytest

import main
import mvcommon
from conftest import FAKE_DUMMY_BYTES

MOVIE = "mov-en-2015-keepsplit"
STEM = "Keep.Split.2015"
SEASON = "tv-en-2020-smk-s01"
EP1, EP2 = SEASON + "e01", SEASON + "e02"
MOVIE_TMDB, SHOW_TMDB = 424242, 70523


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _chunk_names(stem, short_id, n):
    return [f"{stem} [{short_id}].chunk.{i:03d}.mkv" for i in range(1, n + 1)]


def _device_videos(device_dir):
    return {f.name: f for f in device_dir.rglob("*.mkv")}


def _tree(root):
    return sorted((str(p.relative_to(root)), p.is_dir(), None if p.is_dir() else p.stat().st_size)
                  for p in root.rglob("*"))


def _plenty_of_disk(monkeypatch):
    huge = 500 * 1024 ** 3
    monkeypatch.setattr(main.shutil, "disk_usage",
                        lambda path: types.SimpleNamespace(total=huge, used=0, free=huge))


def _install_fake_split(monkeypatch, n_chunks=2):
    """mkvmerge `--split` -> a byte slicer honouring split_video_file's contract (the
    chunks concatenate back to the master); the unsplittable-track probe finds
    nothing; the disk is huge."""
    def fake_split(input_path, output_dir, method, value_str, file_id=""):
        data = _read(input_path)
        step = len(data) // n_chunks + 1
        stem = os.path.splitext(os.path.basename(input_path))[0]
        paths = []
        for i in range(n_chunks):
            p = os.path.join(output_dir, f"{stem} [{file_id}].chunk.{i + 1:03d}.mkv")
            with open(p, "wb") as f:
                f.write(data[i * step:(i + 1) * step])
            paths.append(p)
        return paths

    monkeypatch.setattr(main, "split_video_file", fake_split)
    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda path: [])
    _plenty_of_disk(monkeypatch)


def _install_concat_merge(monkeypatch):
    """mkvmerge merge -> in-order byte concatenation, the inverse of the fake split."""
    def fake_merge(chunk_paths, output_path, seed=None, **_kwargs):
        with open(output_path, "wb") as out:
            for p in chunk_paths:
                out.write(_read(p))
        return True

    monkeypatch.setattr(main, "merge_video_files", fake_merge)


def _fail_first_pushes_of(monkeypatch, needle, times=3):
    """Fail the first `times` `adb push` calls whose argv mentions `needle` (one
    chunk's three retry attempts), then let the fake device behave again."""
    inner = main.subprocess.run
    seen = {"n": 0}

    def run(argv, check=False, **kwargs):
        argv = list(argv)
        if "push" in argv and any(needle in str(a) for a in argv):
            seen["n"] += 1
            if seen["n"] <= times:
                if check:
                    raise subprocess.CalledProcessError(1, argv)
        return inner(argv, check=check, **kwargs)

    monkeypatch.setattr(main.subprocess, "run", run)
    monkeypatch.setattr(mvcommon.time, "sleep", lambda *a, **k: None)


def _new_movie(sandbox, make_video, folder_name="Keep Split (2015)"):
    """A real master in its own title folder; the library is empty."""
    folder = sandbox["local_root"] / "Movies" / folder_name
    folder.mkdir(parents=True)
    master, sha256 = make_video(folder / f"{STEM}.mkv")
    mvcommon.save_library({})
    return folder, master, sha256


def _split_info(chunk_bytes, **extra):
    info = {"is_split": True, "method": "COUNT", "val": str(len(chunk_bytes)), "total_chunks": len(chunk_bytes),
            "chunks": [{"filename": n, "hash": _sha256(b)} for n, b in chunk_bytes.items()]}
    info.update(extra)
    return info


def _interrupted_movie(sandbox, make_video, present=(2,), **split_extra):
    """The state an interrupted split push leaves: a prepped `local_ready` entry that
    records a two-chunk split, with the chunks in `present` still in `_parts/`."""
    folder, master, sha256 = _new_movie(sandbox, make_video)
    assert main.cmd_prep(MOVIE, master) is True          # a real prep: sidecars + entry
    short_id = mvcommon.generate_short_id(MOVIE)
    names = _chunk_names(STEM, short_id, 2)
    data = _read(master)
    half = len(data) // 2 + 1
    chunk_bytes = {names[0]: data[:half], names[1]: data[half:]}
    library = mvcommon.load_library()
    library[MOVIE]["split_info"] = _split_info(chunk_bytes, **split_extra)
    library[MOVIE]["re_hashed"] = False
    mvcommon.save_library(library)
    parts = folder / main.SPLIT_DIR_NAME
    if present:
        parts.mkdir()
        for i in present:
            (parts / names[i - 1]).write_bytes(chunk_bytes[names[i - 1]])
    return folder, master, sha256, names, chunk_bytes


def _restore_from_device(entry_id, device_dir):
    """What `fetch` does, without a browser: stage the objects this entry RECORDS from
    the fake device into its restore dir, then run the real restore."""
    entry = mvcommon.load_library()[entry_id]
    restore = os.path.join(entry["folder_path"], main.RESTORE_DIR_NAME)
    os.makedirs(restore, exist_ok=True)
    on_device = _device_videos(device_dir)
    info = entry.get("split_info") or {}
    if info.get("is_split"):
        for chunk in info["chunks"]:
            shutil.copyfile(on_device[chunk["filename"]], os.path.join(restore, chunk["filename"]))
    else:
        stem, ext = os.path.splitext(entry["filename"])
        shutil.copyfile(on_device[f"{stem} [{entry['short_id']}]{ext}"], os.path.join(restore, entry["filename"]))
    return main.cmd_restore(entry_id)


def _run_cli(command_line):
    """Run one printed MediaVault command line through main.py's real CLI dispatcher
    (the `if __name__ == "__main__":` block), as typing it would. The commands it
    reaches are the real, test-patched ones."""
    argv = [t.strip('"') for t in re.findall(r'"[^"]*"|\S+', command_line)]
    with open(main.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=main.__file__)
    guard = next(n for n in tree.body if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                 and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__")

    class _Sys:
        def __getattr__(self, name):
            return getattr(sys, name)

    proxy = _Sys()
    proxy.argv = ["main.py", *argv]
    namespace = dict(main.__dict__)
    namespace["sys"] = proxy
    exec(compile(ast.Module(body=guard.body, type_ignores=[]), main.__file__, "exec"), namespace)


class _Resp:
    def __init__(self, json_data=None, content=b""):
        self.status_code, self._json, self.content = 200, json_data, content

    def json(self):
        return self._json


def _install_tmdb(monkeypatch, tmp_path, details_by_path):
    """A by-id-only TMDB: `details_by_path` maps '/movie/<id>' or '/tv/<id>' to its
    details; images are bytes; everything else is empty. No network, no real cache."""
    monkeypatch.setattr(main, "TMDB_CACHE_DIR", str(tmp_path / "tmdb_cache"))
    monkeypatch.setattr(main, "EXA_CACHE_DIR", str(tmp_path / "exa_cache"))
    monkeypatch.setattr(mvcommon, "tmdb_api_key", lambda: "TEST-V3-KEY")
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "")

    def get(url, params=None, headers=None, timeout=None, **kwargs):
        if url.startswith("https://image.tmdb.org/t/p/"):
            return _Resp(content=b"\xff\xd8FAKE-JPG\xff\xd9")
        if url.endswith("/configuration"):
            return _Resp({"images": {"secure_base_url": "https://image.tmdb.org/t/p/"}})
        for path, details in details_by_path.items():
            if url.endswith(path):
                return _Resp(details)
        return _Resp({})

    monkeypatch.setattr(main.requests, "get", get)


def _assert_archived_split_and_restorable(entry_id, names, master_bytes, mock_device):
    """The end state every resumed archive must reach: archived, its split record
    intact and matching the cloud, the master dummied, and a real restore works."""
    entry = mvcommon.load_library()[entry_id]
    assert (entry["status"], entry["uploaded"]) == ("archived", True)
    on_device = _device_videos(mock_device)
    assert [c["filename"] for c in entry["split_info"]["chunks"]] == names, "the split record must survive"
    assert [c["hash"] for c in entry["split_info"]["chunks"]] == [_sha256(on_device[n].read_bytes()) for n in names]
    assert b"".join(on_device[n].read_bytes() for n in names) == master_bytes
    master = os.path.join(entry["folder_path"], entry["filename"])
    assert _read(master) == FAKE_DUMMY_BYTES, "replace must have dummied the master"
    assert _restore_from_device(entry_id, mock_device) is True
    assert _read(master) == master_bytes, "the restored file must be the original master"
    assert mvcommon.load_library()[entry_id]["status"] == "restored_local"


# ---------------------------------------------------------------------------
# (1) end to end: an interrupted autopilot, then the SAME command again
# ---------------------------------------------------------------------------

def test_rerunning_prep_push_rep_resumes_an_interrupted_split_push(
        sandbox, mock_device, make_video, stub_tech_specs, fake_dummy, monkeypatch, capsys):
    _install_fake_split(monkeypatch, n_chunks=3)
    _install_concat_merge(monkeypatch)
    folder, master, _ = _new_movie(sandbox, make_video)
    master_bytes = _read(master)
    names = _chunk_names(STEM, mvcommon.generate_short_id(MOVIE), 3)
    _fail_first_pushes_of(monkeypatch, "chunk.002")

    main.cmd_prep_push_rep(MOVIE, master, "COUNT", "3")     # chunk 1 goes up, chunk 2 fails for good

    entry = mvcommon.load_library()[MOVIE]
    assert (entry["status"], entry["uploaded"]) == ("local_ready", False)
    assert [c["filename"] for c in entry["split_info"]["chunks"]] == names
    parts = folder / main.SPLIT_DIR_NAME
    assert set(_device_videos(mock_device)) == {names[0]}
    assert sorted(p.name for p in parts.iterdir()) == names[1:]
    capsys.readouterr()

    main.cmd_prep_push_rep(MOVIE, master, "COUNT", "3")     # the same command again

    out = capsys.readouterr().out
    assert "keeping its split record" in out and "Resuming 2 chunks" in out
    assert set(_device_videos(mock_device)) == set(names), "the remaining recorded chunks must be resumed"
    assert not parts.exists()
    _assert_archived_split_and_restorable(MOVIE, names, master_bytes, mock_device)


def test_the_season_autopilots_own_printed_resume_command_resumes_the_interrupted_episode(
        sandbox, mock_device, make_video, stub_tech_specs, fake_dummy, monkeypatch, capsys):
    """The season autopilot stops at the interrupted episode and prints the command to
    resume the season from it. That exact line, run through the real CLI dispatcher,
    must finish the interrupted episode from its recorded split and carry on."""
    _install_fake_split(monkeypatch, n_chunks=2)
    _install_concat_merge(monkeypatch)
    folder = sandbox["local_root"] / "Series" / "SMK" / "Season 01"
    folder.mkdir(parents=True)
    masters = {}
    for ep_id, filename in ((EP1, "SMK.S01E01.mkv"), (EP2, "SMK.S01E02.mkv")):
        masters[ep_id] = _read(make_video(folder / filename, marker=ep_id.encode())[0])
    mvcommon.save_library({})
    names = {ep: _chunk_names(f"SMK.S01E0{i}", mvcommon.generate_short_id(ep), 2)
             for i, ep in enumerate((EP1, EP2), start=1)}
    _fail_first_pushes_of(monkeypatch, "chunk.002")          # episode 1's second chunk

    main.cmd_prep_push_rep_season(SEASON, str(folder), "COUNT", "2")

    out = capsys.readouterr().out
    resume = re.search(r"Resume the rest of the season: (.+)", out).group(1).strip()
    assert resume.startswith(f'prep_push_rep_season {SEASON} "{folder}" COUNT 2 episodes 01-02')
    library = mvcommon.load_library()
    assert [(library[e]["status"], library[e]["uploaded"]) for e in (EP1, EP2)] == [("local_ready", False)] * 2
    assert set(_device_videos(mock_device)) == {names[EP1][0]}

    _run_cli(resume)

    out = capsys.readouterr().out
    assert "SEASON AUTO-PILOT COMPLETE" in out
    assert set(_device_videos(mock_device)) == set(names[EP1]) | set(names[EP2])
    assert not (folder / main.SPLIT_DIR_NAME).exists()
    for ep_id in (EP2, EP1):
        _assert_archived_split_and_restorable(ep_id, names[ep_id], masters[ep_id], mock_device)


def test_rerunning_prep_push_rep_with_a_tempdir_resumes_from_the_redirected_chunk_dir(
        sandbox, mock_device, make_video, stub_tech_specs, fake_dummy, monkeypatch, tmp_path):
    _install_fake_split(monkeypatch, n_chunks=2)
    _install_concat_merge(monkeypatch)
    folder, master, _ = _new_movie(sandbox, make_video)
    master_bytes = _read(master)
    names = _chunk_names(STEM, mvcommon.generate_short_id(MOVIE), 2)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    parts = scratch / MOVIE / main.SPLIT_DIR_NAME
    _fail_first_pushes_of(monkeypatch, "chunk.002")

    main.cmd_prep_push_rep(MOVIE, master, "COUNT", "2", temp_dir=str(scratch))
    assert sorted(p.name for p in parts.iterdir()) == names[1:]

    main.cmd_prep_push_rep(MOVIE, master, "COUNT", "2", temp_dir=str(scratch))

    assert set(_device_videos(mock_device)) == set(names)
    assert not parts.exists()
    assert not (folder / main.SPLIT_DIR_NAME).exists(), "a redirected push never creates <title>/_parts"
    _assert_archived_split_and_restorable(MOVIE, names, master_bytes, mock_device)


def test_rerunning_prep_push_rep_resumes_a_carried_out_flac_holder_with_its_chunk(
        sandbox, mock_device, make_video, stub_tech_specs, fake_dummy, monkeypatch):
    """The kept record is the whole record. An interrupted FLAC carry-out push left its
    holder beside the last chunk: the re-run must upload both and still record
    `carried_out_tracks`, which is what a restore needs to put the track back."""
    _install_fake_split(monkeypatch, n_chunks=2)
    holder = f"{STEM} [{mvcommon.generate_short_id(MOVIE)}].holder.mkv"
    folder, master, _, names, chunk_bytes = _interrupted_movie(
        sandbox, make_video,
        carried_out_tracks=[{"holder_filename": holder, "holder_hash": _sha256(b"holder-bytes")}])
    (folder / main.SPLIT_DIR_NAME / holder).write_bytes(b"holder-bytes")
    record = mvcommon.load_library()[MOVIE]["split_info"]

    main.cmd_prep_push_rep(MOVIE, master, "COUNT", "2")

    entry = mvcommon.load_library()[MOVIE]
    assert (entry["status"], entry["uploaded"]) == ("archived", True)
    assert entry["split_info"] == record, "the chunks and the carried-out track must both survive the re-prep"
    assert {n: p.read_bytes() for n, p in _device_videos(mock_device).items()} == {
        names[1]: chunk_bytes[names[1]], holder: b"holder-bytes"}
    assert not (folder / main.SPLIT_DIR_NAME).exists()
    assert _read(master) == FAKE_DUMMY_BYTES


def test_rerunning_prep_push_rep_enrich_resumes_then_enriches_and_stays_restorable(
        sandbox, mock_device, make_video, stub_tech_specs, fake_dummy, monkeypatch, tmp_path):
    """The enrich autopilot renames the title folder only after the archive completes.
    The kept split record names chunks, not paths, so it still matches the cloud
    after the rename, and the entry restores from its renamed folder."""
    _install_fake_split(monkeypatch, n_chunks=2)
    _install_concat_merge(monkeypatch)
    _install_tmdb(monkeypatch, tmp_path, {f"/movie/{MOVIE_TMDB}": {
        "id": MOVIE_TMDB, "title": "Keep Split", "release_date": "2015-03-15",
        "poster_path": "/poster.jpg", "backdrop_path": "/backdrop.jpg", "overview": "x", "vote_average": 7.0}})
    folder, master, _ = _new_movie(sandbox, make_video)
    master_bytes = _read(master)
    names = _chunk_names(STEM, mvcommon.generate_short_id(MOVIE), 2)
    _fail_first_pushes_of(monkeypatch, "chunk.002")
    enrich = dict(tmdb_id=MOVIE_TMDB, rename_choice="yes")

    assert main.cmd_prep_push_rep_enrich(MOVIE, master, "COUNT", "2", **enrich) is False
    assert folder.is_dir(), "nothing is renamed before the archive completes"
    assert mvcommon.load_library()[MOVIE]["status"] == "local_ready"

    assert main.cmd_prep_push_rep_enrich(MOVIE, master, "COUNT", "2", **enrich) is True

    stamped = folder.parent / f"{folder.name} {mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=MOVIE_TMDB)}"
    entry = mvcommon.load_library()[MOVIE]
    assert stamped.is_dir() and not folder.exists()
    assert entry["folder_path"] == str(stamped) and entry["metadata"]["tmdb_id"] == MOVIE_TMDB
    assert set(_device_videos(mock_device)) == set(names)
    _assert_archived_split_and_restorable(MOVIE, names, master_bytes, mock_device)


def test_rerunning_prep_push_rep_season_enrich_resumes_then_enriches_and_stays_restorable(
        sandbox, mock_device, make_video, stub_tech_specs, fake_dummy, monkeypatch, tmp_path):
    _install_fake_split(monkeypatch, n_chunks=2)
    _install_concat_merge(monkeypatch)
    _install_tmdb(monkeypatch, tmp_path, {f"/tv/{SHOW_TMDB}": {
        "id": SHOW_TMDB, "name": "SMK", "first_air_date": "2020-01-01",
        "poster_path": "/poster.jpg", "backdrop_path": "/backdrop.jpg", "overview": "x", "vote_average": 8.0}})
    show = sandbox["local_root"] / "Series" / "SMK"
    season = show / "Season 01"
    season.mkdir(parents=True)
    masters = {}
    for ep_id, filename in ((EP1, "SMK.S01E01.mkv"), (EP2, "SMK.S01E02.mkv")):
        masters[ep_id] = _read(make_video(season / filename, marker=ep_id.encode())[0])
    mvcommon.save_library({})
    names = {ep: _chunk_names(f"SMK.S01E0{i}", mvcommon.generate_short_id(ep), 2)
             for i, ep in enumerate((EP1, EP2), start=1)}
    _fail_first_pushes_of(monkeypatch, "chunk.002")
    enrich = dict(tmdb_id=SHOW_TMDB, rename_choice="yes")

    assert main.cmd_prep_push_rep_season_enrich(SEASON, str(season), "COUNT", "2", **enrich) is False
    assert show.is_dir(), "nothing is renamed before the season archives"

    assert main.cmd_prep_push_rep_season_enrich(SEASON, str(season), "COUNT", "2", **enrich) is True

    stamped = show.parent / f"SMK {mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=SHOW_TMDB)}"
    library = mvcommon.load_library()
    assert stamped.is_dir() and not show.exists()
    assert library[EP1]["folder_path"] == str(stamped / "Season 01")
    assert library[EP1]["metadata"]["tmdb_id"] == SHOW_TMDB
    for ep_id in (EP1, EP2):
        _assert_archived_split_and_restorable(ep_id, names[ep_id], masters[ep_id], mock_device)


# ---------------------------------------------------------------------------
# (2) the keep rule itself
# ---------------------------------------------------------------------------

def test_prep_keeps_the_split_record_of_an_unchanged_file(sandbox, make_video, stub_tech_specs, capsys):
    folder, master, _, _, _ = _interrupted_movie(
        sandbox, make_video, merge_seed="seed", canonical_hash="c" * 64,
        carried_out_tracks=[{"holder_filename": "x.holder.mkv", "holder_hash": "d" * 64}])
    before = mvcommon.load_library()[MOVIE]
    assert set(before["split_info"]) == {"is_split", "method", "val", "total_chunks", "chunks",
                                         "merge_seed", "canonical_hash", "carried_out_tracks"}
    parts_before = _tree(folder / main.SPLIT_DIR_NAME)
    capsys.readouterr()

    assert main.cmd_prep(MOVIE, master) is True

    after = mvcommon.load_library()[MOVIE]
    assert after["split_info"] == before["split_info"], "every field of the split record must be kept"
    assert after["re_hashed"] is False, "the split's blessing flag travels with its record"
    assert after == before, "an unchanged re-prep must leave the entry as it was"
    assert _tree(folder / main.SPLIT_DIR_NAME) == parts_before
    assert "keeping its split record (2 chunks)" in capsys.readouterr().out


def test_prep_keeps_the_record_when_only_the_file_name_changed(sandbox, make_video, stub_tech_specs):
    """The hash decides, not the name: the same bytes under a new file name are the
    same master, and the record names chunks, which do not move."""
    folder, master, sha256, _, _ = _interrupted_movie(sandbox, make_video)
    before = mvcommon.load_library()[MOVIE]
    renamed = os.path.join(str(folder), "Keep.Split.2015.REMUX.mkv")
    os.rename(master, renamed)

    assert main.cmd_prep(MOVIE, renamed) is True

    after = mvcommon.load_library()[MOVIE]
    assert after["filename"] == "Keep.Split.2015.REMUX.mkv" and after["hash"] == sha256
    assert after["split_info"] == before["split_info"]


def test_prep_drops_the_split_record_when_the_file_changed_and_the_push_refuses_its_stale_chunks(
        sandbox, mock_device, make_video, stub_tech_specs, monkeypatch, capsys):
    """A different file at the same path is a re-master: the old split describes other
    bytes. Prep drops the record and says so. The old chunks stay where they are (the
    folder may be shared), and the next push refuses them by name."""
    _install_fake_split(monkeypatch)
    folder, master, old_sha, names, _ = _interrupted_movie(sandbox, make_video)
    _, new_sha = make_video(master, marker=b"RE-MASTERED\n")
    assert new_sha != old_sha
    parts = folder / main.SPLIT_DIR_NAME
    parts_before = _tree(parts)
    capsys.readouterr()

    assert main.cmd_prep(MOVIE, master) is True

    after = mvcommon.load_library()[MOVIE]
    assert "split_info" not in after and "re_hashed" not in after
    assert after["hash"] == new_sha
    assert _tree(parts) == parts_before, "prep never deletes chunks"
    out = capsys.readouterr().out
    assert "The file changed since it was split: its old split record is dropped." in out
    assert f"still in {main.SPLIT_DIR_NAME}/ (or your tempdir) are stale. Delete them before pushing." in out

    assert main.cmd_push(MOVIE, "COUNT", "2") is False

    assert list(mock_device.rglob("*.mkv")) == [], "a stale chunk must never be uploaded"
    assert _tree(parts) == parts_before
    out = capsys.readouterr().out
    assert f"{names[1]}  (tagged for this entry, which has no recorded split)" in out
    assert "delete them, then push again to re-split" in out and f"push {MOVIE} COUNT 2" in out


def test_prep_of_an_entry_that_was_never_split_is_unchanged(sandbox, make_video, stub_tech_specs, capsys):
    _, master, _ = _new_movie(sandbox, make_video)
    assert main.cmd_prep(MOVIE, master) is True
    first = mvcommon.load_library()[MOVIE]
    capsys.readouterr()

    assert main.cmd_prep(MOVIE, master) is True

    assert mvcommon.load_library()[MOVIE] == first
    assert "split_info" not in first and "re_hashed" not in first
    out = capsys.readouterr().out
    assert "split record" not in out, "a never-split entry must not print the new notices"


@pytest.mark.parametrize("state", [
    {"status": "onboarded", "uploaded": True},
    {"status": "archived", "uploaded": True},
    {"status": "restored_local", "uploaded": True},
    {"status": "local_ready", "uploaded": True},          # the `uploaded` arm on its own
    {"status": "onboarded", "uploaded": False},           # the status arm on its own
    {"status": "archived", "uploaded": False},
    {"status": "restored_local", "uploaded": False},
], ids=lambda s: f"{s['status']}-{'uploaded' if s['uploaded'] else 'not_uploaded'}")
def test_prep_still_refuses_to_reprep_a_cloud_bearing_entry(sandbox, make_video, stub_tech_specs, capsys, state):
    """The IMP-D4 guard comes first and is untouched: an entry that asserts a cloud
    copy (by its `uploaded` flag or by its status) is never rebuilt, whatever is on
    disk, so the keep rule only ever sees an entry that is still local."""
    _, master, _, _, _ = _interrupted_movie(sandbox, make_video, present=())
    library = mvcommon.load_library()
    library[MOVIE].update(state)
    mvcommon.save_library(library)
    before = mvcommon.load_library()[MOVIE]
    make_video(master, marker=b"DIFFERENT-BYTES\n")
    capsys.readouterr()

    assert main.cmd_prep(MOVIE, master) is True

    assert mvcommon.load_library()[MOVIE] == before
    assert "Skipping Prep" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# (3) rollback: the journal is unchanged, and the split record survives a failed re-prep
# ---------------------------------------------------------------------------

def test_a_reprep_that_fails_while_hashing_leaves_the_entry_untouched(
        sandbox, make_video, stub_tech_specs, monkeypatch):
    folder, master, _, _, _ = _interrupted_movie(sandbox, make_video)
    before = mvcommon.load_library()[MOVIE]
    monkeypatch.setattr(main, "calculate_file_hash", lambda path: "")

    assert main.cmd_prep(MOVIE, master) is False

    assert mvcommon.load_library()[MOVIE] == before
    assert not (folder / main.TXN_JOURNAL_NAME).exists()


def test_a_reprep_that_fails_at_the_save_still_has_its_split_record_after_the_rollback(
        sandbox, make_video, stub_tech_specs, monkeypatch, capsys):
    """The only step after the keep decision that can fail is the save. Prep's rollback
    then persists the library it holds, and the split record is part of it."""
    folder, master, _, _, _ = _interrupted_movie(sandbox, make_video)
    before = mvcommon.load_library()[MOVIE]
    real_save = main.save_library
    calls = {"n": 0}

    def flaky_save(library):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("library file is locked")
        return real_save(library)

    monkeypatch.setattr(main, "save_library", flaky_save)

    assert main.cmd_prep(MOVIE, master) is False

    assert calls["n"] == 2, "prep's save failed, then the rollback saved"
    assert mvcommon.load_library()[MOVIE] == before
    assert not (folder / main.TXN_JOURNAL_NAME).exists()
    assert "Prep failed" in capsys.readouterr().out


def test_prep_journals_exactly_what_it_did_before(sandbox, make_video, stub_tech_specs, monkeypatch):
    """Keeping the record adds nothing to the journal. A first prep records its two
    sidecars and its entry; a re-prep of an existing entry records nothing."""
    ops = []
    real_append = main.RollbackJournal._append

    def spy(self, record):
        ops.append(record["op"])
        return real_append(self, record)

    monkeypatch.setattr(main.RollbackJournal, "_append", spy)

    _, master, _, _, _ = _interrupted_movie(sandbox, make_video)        # a first prep, then a recorded split
    assert ops == ["create_file", "create_file", "create_entry"]
    ops.clear()

    assert main.cmd_prep(MOVIE, master) is True                         # the re-prep that keeps the record

    assert ops == []
    assert "split_info" in mvcommon.load_library()[MOVIE]


def test_a_rerun_whose_fresh_split_fails_before_any_upload_leaves_the_kept_record_in_place(
        sandbox, mock_device, make_video, stub_tech_specs, monkeypatch, capsys):
    """The push journal is untouched too: it records `split_info` only when the run
    itself created it. The leftovers are gone, so the re-run splits afresh; prep kept
    the record, so the push finds one already there, exactly as a plain `push` re-run
    always did. A failure before any upload then rolls back what this run made (its
    `_parts/` and `checksums/`) and never pops a record that was there before it."""
    _install_fake_split(monkeypatch, n_chunks=2)
    folder, master, _, _, _ = _interrupted_movie(sandbox, make_video, present=())
    before = mvcommon.load_library()[MOVIE]
    _fail_first_pushes_of(monkeypatch, "chunk.001")

    main.cmd_prep_push_rep(MOVIE, master, "COUNT", "2")

    out = capsys.readouterr().out
    assert "keeping its split record" in out and "rolling back this-run artifacts" in out
    assert f"Resume with: push {MOVIE}" in out
    assert mvcommon.load_library()[MOVIE] == before, "the record that was there before the push must survive"
    assert not (folder / main.SPLIT_DIR_NAME).exists(), "this run's _parts/ is rolled back"
    assert not (folder / main.CHECKSUM_DIR_NAME).exists()
    assert not (folder / main.TXN_JOURNAL_NAME).exists()
    assert list(mock_device.rglob("*.mkv")) == []


# ---------------------------------------------------------------------------
# (4) a whole-file push never leaves a split record behind
# ---------------------------------------------------------------------------

def test_a_whole_file_push_drops_a_stale_split_record(
        sandbox, mock_device, make_video, stub_tech_specs, fake_dummy, capsys):
    """The entry still records an interrupted (eager) split, its leftovers are gone, and
    the push uploads the file whole. Left in place, the record made `replace` promote
    the split's canonical hash, and `fetch_restore` look for chunks the cloud lacks."""
    _, master, sha256, _, _ = _interrupted_movie(
        sandbox, make_video, present=(), merge_seed="seed", canonical_hash="c" * 64)
    master_bytes = _read(master)
    never_split = {k: v for k, v in mvcommon.load_library()[MOVIE].items() if k not in ("split_info", "re_hashed")}
    capsys.readouterr()

    assert main.cmd_push(MOVIE) is True

    entry = mvcommon.load_library()[MOVIE]
    assert "split_info" not in entry, "the record must describe what this push uploaded: the whole file"
    assert "re_hashed" not in entry, "the split's flag goes with its record"
    assert entry == {**never_split, "uploaded": True, "status": "onboarded"}, \
        "the entry must read exactly like a whole-file archive of a file that was never split"
    assert (entry["status"], entry["uploaded"], entry["hash"]) == ("onboarded", True, sha256)
    tagged = f"{STEM} [{entry['short_id']}].mkv"
    assert {n: p.read_bytes() for n, p in _device_videos(mock_device).items()} == {tagged: master_bytes}
    (sidecar,) = list(mock_device.rglob("*.mvmeta.json"))
    mvmeta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert (mvmeta["is_split"], mvmeta["chunks"]) == (False, [{"filename": tagged, "hash": sha256}])
    assert "uploaded the file whole" in capsys.readouterr().out

    assert main.cmd_replace(MOVIE) is True

    entry = mvcommon.load_library()[MOVIE]
    assert (entry["status"], entry["hash"]) == ("archived", sha256), "no canonical hash may be promoted"
    assert _restore_from_device(MOVIE, mock_device) is True
    assert _read(master) == master_bytes


def test_a_whole_file_repush_of_an_already_uploaded_split_entry_keeps_its_record(
        sandbox, mock_device, make_video, stub_tech_specs, capsys):
    """Unchanged behaviour. A restored split entry already has its chunks in the cloud,
    so its record describes a complete copy and a whole-file re-push leaves it alone."""
    _interrupted_movie(sandbox, make_video, present=())
    library = mvcommon.load_library()
    library[MOVIE].update({"status": "restored_local", "uploaded": True, "re_hashed": True})
    mvcommon.save_library(library)
    record = mvcommon.load_library()[MOVIE]["split_info"]
    capsys.readouterr()

    assert main.cmd_push(MOVIE) is True

    entry = mvcommon.load_library()[MOVIE]
    assert entry["split_info"] == record and entry["re_hashed"] is True
    assert "split record" not in capsys.readouterr().out


def test_rerunning_the_autopilot_without_a_split_size_archives_the_whole_file(
        sandbox, mock_device, make_video, stub_tech_specs, fake_dummy, monkeypatch):
    """After an interrupted split push the leftovers are deleted and the autopilot is
    re-run with no split size. Prep keeps the record (the file is unchanged), the
    push uploads the file whole, and the entry must end as a restorable whole file."""
    _install_fake_split(monkeypatch, n_chunks=2)
    folder, master, sha256 = _new_movie(sandbox, make_video)
    master_bytes = _read(master)
    _fail_first_pushes_of(monkeypatch, "chunk.002")
    main.cmd_prep_push_rep(MOVIE, master, "COUNT", "2")
    assert "split_info" in mvcommon.load_library()[MOVIE]
    shutil.rmtree(folder / main.SPLIT_DIR_NAME)

    main.cmd_prep_push_rep(MOVIE, master)

    entry = mvcommon.load_library()[MOVIE]
    assert (entry["status"], entry["uploaded"], entry["hash"]) == ("archived", True, sha256)
    assert "split_info" not in entry and "re_hashed" not in entry
    assert _read(master) == FAKE_DUMMY_BYTES
    assert _restore_from_device(MOVIE, mock_device) is True
    assert _read(master) == master_bytes


def test_a_whole_file_push_that_fails_leaves_the_entry_and_its_record_untouched(
        sandbox, mock_device, make_video, stub_tech_specs, monkeypatch):
    _interrupted_movie(sandbox, make_video, present=())
    before = mvcommon.load_library()[MOVIE]
    _fail_first_pushes_of(monkeypatch, STEM, times=99)

    assert main.cmd_push(MOVIE) is False

    assert mvcommon.load_library()[MOVIE] == before


def test_a_range_push_of_the_whole_file_does_not_touch_the_record(
        sandbox, mock_device, make_video, stub_tech_specs):
    """`chunks N-M` never marks an entry uploaded, so it makes no claim about what
    the cloud holds and leaves the record alone."""
    _interrupted_movie(sandbox, make_video, present=())
    before = mvcommon.load_library()[MOVIE]

    assert main.cmd_push(MOVIE, chunk_range="1-2") is True

    assert mvcommon.load_library()[MOVIE] == before
