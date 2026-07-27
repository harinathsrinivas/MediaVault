"""IMP-D19 — extras (Specials / Trailers / Behind-the-Scenes) lifecycle battery.

The permanent regression net for the whole extras feature. Every test asserts
OBSERVABLE contract behavior — files on disk, files on the fake device, item
`status`/`uploaded`, the library JSON after a real save/load — rather than
implementation internals, so a later refactor of the extras code cannot
false-fail this file.

Coverage map (PLAN Step 10, cases (a)-(g)); each test's docstring opens with its
letter:
  (a) scan / merge / dedup idempotence — Specials-then-Trailers == both at once,
      re-add is a no-op (and can NOT un-archive), changed bytes reset `uploaded`
  (b) push — chunks/whole files land on `mock_device`, statuses flip, and the
      INDEPENDENT `--extras-size` (explicit / inherited / `none`) is honored
  (c) replace — every uploaded extra becomes a `fake_dummy`, status `archived`
  (d) restore — verified whole-file placement + split re-merge into the
      (re)created `Specials/` subfolder, plus corrupt-chunk quarantine
  (e) `parse_extras_tokens` parsing (locked Card B1 forms + the fail-fast paths)
  (f) `add_extras` on an archived-main title processes ONLY extras
  (g) `rename_folder` keeps extras valid (`group_rel`/`sub_rel` are relative)
  (h) the two AUTOPILOTS with `--extras` — one-shot register -> push -> dummy on a
      BRAND-NEW title, and the flag-gated no-extras path (Step 12b)
  (i) the D19-B1 LAYERED GUARD (Step 12c) — a re-scan over an ARCHIVED group can
      NOT clobber cloud-bearing items with dummy hashes, `push_one_extra` refuses
      dummy-sized uploads, and a new dummy-sized file is never registered

Fixtures are the documented ones (docs/testing-strategy.md §4): `sandbox_extras`
(§4.9, inheriting `sandbox`'s dual LIBRARY_*/LOCAL_ROOT patch and its C:\\Media
hard-guard), `mock_device` (§4.4), `fake_dummy` (§4.3), `stub_tech_specs`,
`make_video`. The (h) autopilot cases start from the BARE `sandbox` instead — the
whole point is a title that does NOT pre-exist, so they let `cmd_prep` /
`cmd_prep_season` create the entry (and the extras block) themselves. Nothing here
touches real C:\\Media or real library_*.json — every path lives under the sandbox
tmp_path.

THREE deliberate boundary stubs keep the battery deterministic and fast:
  * `_install_fake_split` replaces `main.split_video_file`. The fixture extras are
    ~264 KB and `split_video_file` adds a +10 MB per-chunk buffer, so they can
    NEVER yield >=2 real chunks (the same suite-wide constraint that motivated
    conftest's `ffmpeg_splittable_master_mkv`; recorded as Step 9's caveat). The
    stub SLICES THE REAL BYTES, so the chunks concatenate back to the master.
  * `_install_concat_merge` replaces `main.merge_video_files` (mkvmerge) with an
    in-order byte concatenation — the exact inverse of the slicer — so a restored
    split extra is byte-identical to the pre-push master and the real
    hash-verify/bless path is exercised for genuine integrity, without mkvmerge.
  * `plenty_of_disk` neutralizes the free-space pre-flights at their I/O boundary
    (`shutil.disk_usage`), because `_disk_buffer`'s 2 GB floor would otherwise
    make the split/merge paths depend on the host's free disk.

Device-name lookups always go through `_device_names` — never rglob a bracketed
pattern, `[short_id]` is a glob character class (testing-strategy §8.1).
"""
import copy
import os
import shutil
import types
from datetime import datetime

import pytest

import main
import mainfetch
import mvcommon
from conftest import FAKE_DUMMY_BYTES

# ===========================================================================
# Helpers
# ===========================================================================


def _items(library, title_id, group_rel="Specials"):
    """The stored item list of one extras group, in library order."""
    return library[title_id]["extras"]["groups"][group_rel]["items"]


def _by_sub_rel(library, title_id, group_rel="Specials"):
    """{sub_rel: item} view of one extras group."""
    return {it["sub_rel"]: it for it in _items(library, title_id, group_rel)}


def _device_names(device_dir, pattern="*.mkv"):
    """{filename: Path} index of the fake device.

    Always index by `.name` — MediaVault filenames carry "[short_id]", and
    rglob would read the brackets as a glob character class and silently match
    nothing (docs/testing-strategy.md §8.1 / §9).
    """
    return {f.name: f for f in device_dir.rglob(pattern)}


def _remote_name(item):
    """The UID-tagged name a WHOLE-file extra gets on the device."""
    base, ext = os.path.splitext(item["filename"])
    return f"{base} [{item['short_id']}]{ext}"


def _install_fake_split(monkeypatch, n_chunks=2):
    """Replace `main.split_video_file` with a deterministic byte-slicer.

    Reproduces the real function's contract — chunk names
    `"<base> [<file_id>].chunk.NNN.mkv"` inside `output_dir`, returned in order —
    but slices the input's REAL bytes instead of invoking mkvmerge, so
    concatenating the chunks reproduces the master exactly (see the module
    docstring for why a genuine split is impossible at fixture scale).

    Returns the recorded call list, so a test can assert WHICH method/val/file_id
    the extras push actually passed down (the independent-`--extras-size` proof).
    """
    calls = []

    def fake_split(input_path, output_dir, method, value_str, file_id=""):
        calls.append({"input": input_path, "output_dir": output_dir,
                      "method": method, "val": value_str, "file_id": file_id})
        os.makedirs(output_dir, exist_ok=True)
        with open(input_path, "rb") as f:
            data = f.read()
        step = len(data) // n_chunks + 1  # +1 => no empty trailing slice
        base = os.path.splitext(os.path.basename(input_path))[0]
        tag = f" [{file_id}]" if file_id else ""
        paths = []
        for i in range(n_chunks):
            p = os.path.join(output_dir, f"{base}{tag}.chunk.{i + 1:03d}.mkv")
            with open(p, "wb") as f:
                f.write(data[i * step:(i + 1) * step])
            paths.append(p)
        return paths

    monkeypatch.setattr(main, "split_video_file", fake_split)
    return calls


def _install_concat_merge(monkeypatch):
    """Replace `main.merge_video_files` (mkvmerge) with an in-order byte
    concatenation — the inverse of `_install_fake_split`'s slicer. Returns the
    recorded call list (chunks / out / seed)."""
    calls = []

    def fake_merge(chunk_paths, output_path, seed=None):
        calls.append({"chunks": list(chunk_paths), "out": output_path, "seed": seed})
        with open(output_path, "wb") as out:
            for p in chunk_paths:
                with open(p, "rb") as f:
                    out.write(f.read())
        return True

    monkeypatch.setattr(main, "merge_video_files", fake_merge)
    return calls


def _stage_for_restore(extra_folder, sources):
    """Stage downloads exactly as the fetch leg does, then hand back the folder.

    `mainfetch.fetch_single_entry` makes `<entry folder_path>/restore/` and
    `shutil.move`s each hash-matched download to `<that>/<queue filename>` — for
    an extras synthetic entry (`build_extras_entries`) that folder is
    `<title folder_path>/<group_rel>/`, i.e. exactly where `restore_one_extra`
    looks. `sources` maps the staged filename -> a path to copy the bytes from.
    """
    restore_dir = os.path.join(str(extra_folder), main.RESTORE_DIR_NAME)
    os.makedirs(restore_dir, exist_ok=True)
    for name, src in sources.items():
        shutil.copy2(str(src), os.path.join(restore_dir, name))
    return restore_dir


def _archive_main_content(sandbox_extras):
    """Put the title's MAIN content in the post-`cmd_replace` archived state (a
    tiny dummy on disk, status=archived, uploaded=True) and return a deep copy of
    every NON-extras key of the entry, for an exact "main untouched" comparison.
    """
    library = mvcommon.load_library()
    entry = library[sandbox_extras["title_id"]]
    sandbox_extras["orig_path"].write_bytes(FAKE_DUMMY_BYTES)
    entry["status"] = "archived"
    entry["uploaded"] = True
    mvcommon.save_library(library)
    return copy.deepcopy({k: v for k, v in entry.items() if k != "extras"})


@pytest.fixture()
def plenty_of_disk(monkeypatch):
    """Neutralize the split/merge free-space pre-flights at the I/O boundary.

    `_free_space_ok` -> `_disk_shortfall` -> `shutil.disk_usage(dir).free`, and
    `_disk_buffer` imposes a 2 GB floor, so a split push / split restore of even a
    264 KB extra "requires" ~2 GB. Patching `disk_usage` (the boundary, not the
    decision helper) keeps those paths reachable on any host without changing the
    logic under test. `shutil.copy2`/`move` are untouched.
    """
    huge = 500 * 1024 ** 3
    monkeypatch.setattr(
        main.shutil, "disk_usage",
        lambda path: types.SimpleNamespace(total=huge, used=0, free=huge),
    )


# ===========================================================================
# (a) SCAN / MERGE / DEDUP — the additive, idempotent data core
# ===========================================================================


def test_scan_collects_videos_recursively_with_posix_sub_rel(
        sandbox_extras, stub_tech_specs, make_video):
    """(a) One folder spec = ONE group keyed by its path relative to the title
    folder; every video inside becomes an item with a posix `sub_rel` (nested
    included, sorted), non-video files are ignored, internal working dirs are
    pruned (a leftover staged download must never re-register as a new extra),
    and the scan-derived fields carry the documented pre-push values."""
    media_dir = sandbox_extras["media_dir"]
    title_id = sandbox_extras["title_id"]

    trailers = media_dir / "Trailers"
    (trailers / "Bonus").mkdir(parents=True)
    make_video(trailers / "Teaser.mkv", marker=b"TEASER\n")
    _, deep_hash = make_video(trailers / "Bonus" / "Deep.mkv", marker=b"DEEP\n")
    (trailers / "notes.txt").write_bytes(b"not a video")
    staged = trailers / main.RESTORE_DIR_NAME
    staged.mkdir()
    make_video(staged / "Teaser.mkv", marker=b"ALREADY-STAGED\n")

    scanned = main.scan_extras_folders([str(trailers)], str(media_dir), title_id)

    assert list(scanned) == ["Trailers"]
    items = scanned["Trailers"]["items"]
    assert [it["sub_rel"] for it in items] == ["Bonus/Deep.mkv", "Teaser.mkv"], \
        "items must be sorted by posix sub_rel; notes.txt and restore/ excluded"

    deep = items[0]
    assert deep["filename"] == "Deep.mkv"
    assert deep["hash"] == deep_hash
    assert deep["status"] == "local_ready" and deep["uploaded"] is False
    assert deep["short_id"] == mvcommon.generate_short_id(
        f"{title_id}::Trailers/Bonus/Deep.mkv")
    assert deep["search_term"] == f"Deep [{deep['short_id']}].mkv"
    assert deep["tech_spec"]["size_bytes"] == os.path.getsize(
        trailers / "Bonus" / "Deep.mkv")


def test_scan_dedups_the_same_folder_passed_twice(sandbox_extras, stub_tech_specs):
    """(a) `--extras "A;A"` (or a repeated flag naming the same folder) collapses
    into ONE group with each file registered exactly once — dedup is per group by
    normalized `sub_rel`."""
    scanned = main.scan_extras_folders(
        [str(sandbox_extras["extras_dir"]), str(sandbox_extras["extras_dir"])],
        str(sandbox_extras["media_dir"]), sandbox_extras["title_id"])

    assert list(scanned) == ["Specials"]
    assert [it["sub_rel"] for it in scanned["Specials"]["items"]] == \
        ["BTS.mkv", "Trailer.mkv"]


def test_specials_then_trailers_equals_both_at_once(
        sandbox_extras, stub_tech_specs, make_video):
    """(a) Additive identity: merging Specials and THEN Trailers produces the
    same extras block as merging both in one shot, so a later `add_extras` can
    never diverge from one big run. (Both paths run on the same title id, so the
    short_id seeds match; both stamp today's `added_date`.)"""
    media_dir = sandbox_extras["media_dir"]
    title_id = sandbox_extras["title_id"]
    specials = str(sandbox_extras["extras_dir"])

    trailers = media_dir / "Trailers"
    trailers.mkdir()
    make_video(trailers / "Teaser.mkv", marker=b"TEASER\n")
    trailers = str(trailers)

    # Two independent in-memory libraries (merge is a pure dict operation).
    lib_seq = {title_id: {"folder_path": str(media_dir)}}
    lib_once = {title_id: {"folder_path": str(media_dir)}}

    main.merge_extras_into_title(lib_seq, title_id, main.scan_extras_folders(
        [specials], str(media_dir), title_id))
    main.merge_extras_into_title(lib_seq, title_id, main.scan_extras_folders(
        [trailers], str(media_dir), title_id))
    main.merge_extras_into_title(lib_once, title_id, main.scan_extras_folders(
        [specials, trailers], str(media_dir), title_id))

    assert sorted(lib_once[title_id]["extras"]["groups"]) == ["Specials", "Trailers"]
    assert lib_seq[title_id]["extras"] == lib_once[title_id]["extras"]


def test_scan_merge_and_save_persists_a_new_group(
        sandbox_extras, stub_tech_specs, make_video):
    """(a) The production prep wrapper (`_extras_scan_merge_and_save`, what
    cmd_prep / cmd_add_extras call) resolves the title, merges AND persists: a
    reload shows both groups, the pre-existing group byte-for-byte untouched, and
    the new group stamped with today's `added_date`."""
    title_id = sandbox_extras["title_id"]
    trailers = sandbox_extras["media_dir"] / "Trailers"
    trailers.mkdir()
    make_video(trailers / "Teaser.mkv", marker=b"TEASER\n")

    library = mvcommon.load_library()
    specials_before = copy.deepcopy(_items(library, title_id))

    main._extras_scan_merge_and_save(library, title_id, [str(trailers)])

    groups = mvcommon.load_library()[title_id]["extras"]["groups"]
    assert sorted(groups) == ["Specials", "Trailers"]
    assert groups["Specials"]["items"] == specials_before
    assert [it["sub_rel"] for it in groups["Trailers"]["items"]] == ["Teaser.mkv"]
    assert groups["Trailers"]["added_date"] == datetime.now().strftime("%Y-%m-%d")


def test_re_merging_an_unchanged_scan_is_a_no_op(sandbox_extras, stub_tech_specs):
    """(a) Re-adding an unchanged folder is idempotent — the persisted block is
    byte-for-byte unchanged — and, the property that matters operationally, it
    must NOT drag an already-uploaded/archived extra back to the pre-push
    state."""
    title_id = sandbox_extras["title_id"]

    library = mvcommon.load_library()
    for it in _items(library, title_id):        # advance the group's lifecycle
        it["uploaded"] = True
        it["status"] = "archived"
    mvcommon.save_library(library)

    library = mvcommon.load_library()
    before = copy.deepcopy(library[title_id]["extras"])

    main.merge_extras_into_title(library, title_id, main.scan_extras_folders(
        [str(sandbox_extras["extras_dir"])], str(sandbox_extras["media_dir"]),
        title_id))
    mvcommon.save_library(library)

    assert mvcommon.load_library()[title_id]["extras"] == before


def test_changed_bytes_reset_uploaded_and_drop_stale_split_info(
        sandbox_extras, stub_tech_specs, make_video):
    """(a) A re-scan that finds CHANGED bytes returns that item to the unblessed
    needs-re-push state (hash refreshed, uploaded=False, status=local_ready) and
    DROPS the now-stale `split_info` / `re_hashed`; its sibling is untouched."""
    title_id = sandbox_extras["title_id"]

    library = mvcommon.load_library()
    bts = _by_sub_rel(library, title_id)["BTS.mkv"]
    bts.update({
        "uploaded": True,
        "status": "archived",
        "re_hashed": True,
        "split_info": {"is_split": True, "method": "COUNT", "val": "2",
                       "total_chunks": 1, "chunks": [{"filename": "x", "hash": "y"}]},
    })
    trailer_before = copy.deepcopy(_by_sub_rel(library, title_id)["Trailer.mkv"])

    _, new_hash = make_video(sandbox_extras["extras_dir"] / "BTS.mkv",
                             marker=b"EXTRA-BTS-REENCODED\n")

    main.merge_extras_into_title(library, title_id, main.scan_extras_folders(
        [str(sandbox_extras["extras_dir"])], str(sandbox_extras["media_dir"]),
        title_id))

    after = _by_sub_rel(library, title_id)
    assert after["BTS.mkv"]["hash"] == new_hash
    assert after["BTS.mkv"]["uploaded"] is False
    assert after["BTS.mkv"]["status"] == "local_ready"
    assert "split_info" not in after["BTS.mkv"]
    assert "re_hashed" not in after["BTS.mkv"]
    assert after["Trailer.mkv"] == trailer_before


# ===========================================================================
# (b) PUSH — mock_device + the independent --extras-size
# ===========================================================================


def test_push_whole_file_lands_on_device_and_flips_status(sandbox_extras, mock_device):
    """(b) A whole-file extras push copies each extra to the device under its
    UID-tagged name inside a remote folder that MIRRORS the `Specials/`
    subfolder, writes the mvmeta sidecar, leaves no `.partial`, and flips each
    item to uploaded/onboarded — while the title's MAIN content is neither
    pushed nor modified (Card E1)."""
    title_id = sandbox_extras["title_id"]
    library = mvcommon.load_library()

    assert main.push_title_extras(library, title_id, ("NONE", None)) is True

    on_device = _device_names(mock_device)
    for it in sandbox_extras["items"]:
        remote = _remote_name(it)
        assert remote in on_device, f"{remote} not on device: {sorted(on_device)}"
        assert on_device[remote].read_bytes() == it["path"].read_bytes()
        assert on_device[remote].parent.name == "Specials"
    assert [n for n in on_device if n.startswith("extras_movie")] == [], \
        "the title's main content must NOT be uploaded by an extras push"
    assert list(mock_device.rglob(f"*{main.PARTIAL_SUFFIX}")) == []
    assert len(list(mock_device.rglob("*.mvmeta.json"))) == 2

    reloaded = mvcommon.load_library()
    for it in _items(reloaded, title_id):
        assert it["uploaded"] is True and it["status"] == "onboarded"
        assert "split_info" not in it, "a whole-file push must not stamp split_info"
    assert reloaded[title_id]["status"] == "local_ready"
    assert reloaded[title_id]["uploaded"] is False
    assert mvcommon.calculate_file_hash(str(sandbox_extras["orig_path"])) == \
        reloaded[title_id]["hash"]


def test_push_honors_an_independent_extras_size_split(
        sandbox_extras, mock_device, plenty_of_disk, monkeypatch):
    """(b) An explicit `--extras-size` splits the extras even when the command
    has NO main split: the chunks (not the master) land on the device with their
    per-item `[short_id]` tag, `split_info` records the extras' own method/val +
    a hash per chunk, and the local chunk dir is cleaned up afterwards."""
    title_id = sandbox_extras["title_id"]
    calls = _install_fake_split(monkeypatch, n_chunks=2)
    library = mvcommon.load_library()

    assert main.push_title_extras(library, title_id, ("COUNT", "2"),
                                  split_method=None, split_val=None) is True

    assert [c["method"] for c in calls] == ["COUNT", "COUNT"]
    assert [c["val"] for c in calls] == ["2", "2"]
    assert {c["file_id"] for c in calls} == {it["short_id"] for it in sandbox_extras["items"]}

    on_device = _device_names(mock_device)
    for it in sandbox_extras["items"]:
        base = os.path.splitext(it["filename"])[0]
        chunks = [f"{base} [{it['short_id']}].chunk.{i:03d}.mkv" for i in (1, 2)]
        assert all(n in on_device for n in chunks), \
            f"missing chunks for {it['filename']}: {sorted(on_device)}"
        assert _remote_name(it) not in on_device, "the master must not be pushed too"
        merged = b"".join(on_device[n].read_bytes() for n in chunks)
        assert merged == it["path"].read_bytes(), "chunk bytes must reproduce the master"

    for it in _items(mvcommon.load_library(), title_id):
        info = it["split_info"]
        assert info["is_split"] is True
        assert (info["method"], info["val"]) == ("COUNT", "2")
        assert info["total_chunks"] == 2 and len(info["chunks"]) == 2
        assert all(len(c["hash"]) == 64 for c in info["chunks"])
        assert it["uploaded"] is True and it["status"] == "onboarded"

    parts_root = sandbox_extras["extras_dir"] / main.SPLIT_DIR_NAME
    assert list(parts_root.rglob("*.mkv")) == [], "local chunks must be deleted"
    for it in sandbox_extras["items"]:
        assert it["path"].exists(), "the master never leaves its path (O-1 resumable)"


def test_extras_size_none_overrides_the_commands_main_split(
        sandbox_extras, mock_device, monkeypatch):
    """(b) `--extras-size none` WINS over the command's main split: nothing is
    split (`split_video_file` is never called) and the whole file is pushed."""
    title_id = sandbox_extras["title_id"]
    calls = _install_fake_split(monkeypatch)
    library = mvcommon.load_library()

    assert main.push_title_extras(library, title_id, ("NONE", None),
                                  split_method="COUNT", split_val="2") is True

    assert calls == [], "('NONE', None) must suppress the inherited main split"
    on_device = _device_names(mock_device)
    assert [n for n in on_device if ".chunk." in n] == []
    assert all("split_info" not in it for it in _items(mvcommon.load_library(), title_id))


def test_extras_inherit_the_main_split_when_no_extras_size_given(
        sandbox_extras, mock_device, plenty_of_disk, monkeypatch):
    """(b) `extras_size=None` INHERITS the command's main split (the documented
    default) — the extras are split with the main method/val, not whole-pushed."""
    title_id = sandbox_extras["title_id"]
    calls = _install_fake_split(monkeypatch, n_chunks=3)
    library = mvcommon.load_library()

    assert main.push_title_extras(library, title_id, None,
                                  split_method="COUNT", split_val="3") is True

    assert [c["val"] for c in calls] == ["3", "3"]
    for it in _items(mvcommon.load_library(), title_id):
        assert (it["split_info"]["method"], it["split_info"]["val"]) == ("COUNT", "3")
        assert it["split_info"]["total_chunks"] == 3
    assert len([n for n in _device_names(mock_device) if ".chunk." in n]) == 6


def test_push_failure_of_one_extra_does_not_block_the_rest(
        sandbox_extras, mock_device, make_video):
    """(b) A missing-on-disk extra fails ONLY itself: it stays uploaded=False /
    local_ready for a later retry while its sibling completes, the batch reports
    False, and re-running after the file is back finishes the job."""
    title_id = sandbox_extras["title_id"]
    bts, trailer = sandbox_extras["items"]
    os.remove(bts["path"])

    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("NONE", None)) is False

    after = _by_sub_rel(mvcommon.load_library(), title_id)
    assert after["BTS.mkv"]["uploaded"] is False
    assert after["BTS.mkv"]["status"] == "local_ready"
    assert after["Trailer.mkv"]["uploaded"] is True
    assert after["Trailer.mkv"]["status"] == "onboarded"
    assert _remote_name(trailer) in _device_names(mock_device)

    make_video(bts["path"], marker=b"EXTRA-BTS\n")   # same bytes -> same hash
    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("NONE", None)) is True
    assert _remote_name(bts) in _device_names(mock_device)


def test_push_of_an_already_uploaded_group_is_a_silent_success(
        sandbox_extras, mock_device):
    """(b) Re-pushing a fully-uploaded group is a no-op that still reports
    success — nothing is re-uploaded (the device state is unchanged)."""
    title_id = sandbox_extras["title_id"]
    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("NONE", None)) is True
    first = {n: p.read_bytes() for n, p in _device_names(mock_device).items()}

    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("NONE", None)) is True

    assert {n: p.read_bytes() for n, p in _device_names(mock_device).items()} == first


# ===========================================================================
# (c) REPLACE — dummy swap + status archived (fake_dummy)
# ===========================================================================


def test_replace_turns_every_uploaded_extra_into_a_dummy(
        sandbox_extras, mock_device, fake_dummy):
    """(c) After the push, `replace_title_extras` swaps each extra for a tiny
    video dummy and marks it `archived` — `uploaded` stays True (the cloud copy
    lives on), no `.tobedeleted` / `.dummy_tmp` / journal residue is left, and
    the title's MAIN file and entry are untouched (Card E1)."""
    title_id = sandbox_extras["title_id"]
    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("NONE", None)) is True

    assert main.replace_title_extras(library, title_id) is True

    for it in sandbox_extras["items"]:
        assert it["path"].read_bytes() == FAKE_DUMMY_BYTES
        assert os.path.getsize(it["path"]) < main.DUMMY_MAX_BYTES

    reloaded = mvcommon.load_library()
    for it in _items(reloaded, title_id):
        assert it["status"] == "archived" and it["uploaded"] is True

    residue = sorted(p.name for p in sandbox_extras["extras_dir"].iterdir()
                     if ".tobedeleted" in p.name or ".dummy_tmp" in p.name
                     or p.name == main.TXN_JOURNAL_NAME)
    assert residue == []

    assert os.path.getsize(sandbox_extras["orig_path"]) > main.DUMMY_MAX_BYTES
    assert mvcommon.calculate_file_hash(str(sandbox_extras["orig_path"])) == \
        reloaded[title_id]["hash"]
    assert reloaded[title_id]["status"] == "local_ready"


def test_replace_skips_a_not_yet_uploaded_extra_and_is_idempotent(
        sandbox_extras, mock_device, fake_dummy):
    """(c) An extra that was never uploaded is SKIPPED — its real bytes are the
    only copy and must never be destroyed; and an already-archived extra is a
    no-op (no second dummy swap)."""
    title_id = sandbox_extras["title_id"]
    group_rel = sandbox_extras["group_rel"]
    library = mvcommon.load_library()

    bts_item = _by_sub_rel(library, title_id)["BTS.mkv"]
    real_bytes = sandbox_extras["items"][0]["path"].read_bytes()
    assert main.replace_one_extra(library, title_id, group_rel, bts_item) is False
    assert sandbox_extras["items"][0]["path"].read_bytes() == real_bytes
    assert bts_item["status"] == "local_ready"

    assert main.push_title_extras(library, title_id, ("NONE", None)) is True
    assert main.replace_title_extras(library, title_id) is True
    dummies = {it["path"]: it["path"].read_bytes() for it in sandbox_extras["items"]}

    assert main.replace_title_extras(library, title_id) is True
    for path, data in dummies.items():
        assert path.read_bytes() == data
    for it in _items(mvcommon.load_library(), title_id):
        assert it["status"] == "archived"


# ===========================================================================
# (d) RESTORE — verified placement into the (re)created Specials/ subfolder
# ===========================================================================


def test_whole_file_restore_places_verified_bytes_back_in_the_group_folder(
        sandbox_extras, mock_device, fake_dummy):
    """(d) Whole-file lifecycle end to end: push -> replace (dummy) -> the fetch
    leg stages the cloud copy under `<title>/Specials/restore/` -> restore puts
    the hash-VERIFIED bytes back at `<title>/Specials/<sub_rel>`, flips the item
    to `restored_local` (uploaded stays True) and removes the emptied `restore/`.
    The staging folder comes from `mainfetch.build_extras_entries` — the very
    seam `--fetchExtras` uses — so this pins the fetch->restore contract."""
    title_id = sandbox_extras["title_id"]
    originals = {it["filename"]: it["path"].read_bytes() for it in sandbox_extras["items"]}

    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("NONE", None)) is True
    assert main.replace_title_extras(library, title_id) is True

    fetch_entries = mainfetch.build_extras_entries(mvcommon.load_library()[title_id])
    assert len(fetch_entries) == 2, "both cloud-resident extras must be fetchable"
    on_device = _device_names(mock_device)
    for fe in fetch_entries:
        base, ext = os.path.splitext(fe["filename"])
        _stage_for_restore(fe["folder_path"],
                           {fe["filename"]: on_device[f"{base} [{fe['short_id']}]{ext}"]})

    library = mvcommon.load_library()
    assert main.restore_title_extras(library, title_id) == 2

    reloaded = mvcommon.load_library()
    for _group, path, item in main._extras_item_paths(reloaded[title_id]):
        assert item["status"] == "restored_local" and item["uploaded"] is True
        with open(path, "rb") as f:
            assert f.read() == originals[item["filename"]]
        assert mvcommon.calculate_file_hash(path) == item["hash"]
    assert not (sandbox_extras["extras_dir"] / main.RESTORE_DIR_NAME).exists()


def test_restore_places_the_extra_into_a_recreated_group_folder(
        sandbox_extras, mock_device, fake_dummy):
    """(d) The "recreated `Specials/`" case: the whole group folder is gone after
    archiving (deleted by the user / a media-server cleanup). The fetch leg
    recreates it implicitly (`os.makedirs(<extra folder>/restore)`), and restore
    then places the verified file at its canonical path even though no dummy is
    there to overwrite."""
    title_id = sandbox_extras["title_id"]
    originals = {it["filename"]: it["path"].read_bytes() for it in sandbox_extras["items"]}

    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("NONE", None)) is True
    assert main.replace_title_extras(library, title_id) is True

    # Capture the cloud bytes BEFORE deleting the folder (a list, not a dict —
    # both extras of a group legitimately share ONE staging folder).
    on_device = _device_names(mock_device)
    staged_sources = []
    for fe in mainfetch.build_extras_entries(mvcommon.load_library()[title_id]):
        base, ext = os.path.splitext(fe["filename"])
        staged_sources.append((fe["folder_path"], fe["filename"],
                               on_device[f"{base} [{fe['short_id']}]{ext}"].read_bytes()))
    assert len(staged_sources) == 2

    shutil.rmtree(sandbox_extras["extras_dir"])
    assert not sandbox_extras["extras_dir"].exists()

    for folder, name, data in staged_sources:
        restore_dir = os.path.join(folder, main.RESTORE_DIR_NAME)
        os.makedirs(restore_dir, exist_ok=True)      # what fetch_single_entry does
        with open(os.path.join(restore_dir, name), "wb") as f:
            f.write(data)

    library = mvcommon.load_library()
    assert main.restore_title_extras(library, title_id) == 2

    reloaded = mvcommon.load_library()
    assert sandbox_extras["extras_dir"].is_dir()
    for _group, path, item in main._extras_item_paths(reloaded[title_id]):
        assert os.path.isfile(path), f"extra not placed into the recreated folder: {path}"
        with open(path, "rb") as f:
            assert f.read() == originals[item["filename"]]
        assert item["status"] == "restored_local"


def test_split_restore_remerges_and_blesses_the_canonical_hash(
        sandbox_extras, mock_device, fake_dummy, plenty_of_disk, monkeypatch):
    """(d) Split lifecycle end to end: an independently-sized extras push splits
    and uploads chunks -> replace dummies the extra -> the chunks are staged in
    `restore/` under their `split_info` names -> restore verifies EVERY chunk
    hash, re-merges into a temp sibling, blesses the merged hash (re_hashed +
    merge_seed/merge_tool/rehashed_at) and only then swaps it over the dummy.
    Slice-then-concat stubs make the restored file byte-identical to the pre-push
    master, so the stored hash must come back UNCHANGED — a genuine integrity
    assertion, not just a status check."""
    title_id = sandbox_extras["title_id"]
    originals = {it["filename"]: it["path"].read_bytes() for it in sandbox_extras["items"]}
    hashes = {it["filename"]: it["hash"] for it in sandbox_extras["items"]}

    _install_fake_split(monkeypatch, n_chunks=2)
    merges = _install_concat_merge(monkeypatch)

    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("COUNT", "2")) is True
    assert main.replace_title_extras(library, title_id) is True

    on_device = _device_names(mock_device)
    library = mvcommon.load_library()
    for _group, path, item in main._extras_item_paths(library[title_id]):
        assert os.path.getsize(path) < main.DUMMY_MAX_BYTES, "archived dummy expected"
        names = [c["filename"] for c in item["split_info"]["chunks"]]
        _stage_for_restore(os.path.dirname(path), {n: on_device[n] for n in names})

    assert main.restore_title_extras(library, title_id) == 2
    assert len(merges) == 2

    reloaded = mvcommon.load_library()
    for _group, path, item in main._extras_item_paths(reloaded[title_id]):
        with open(path, "rb") as f:
            assert f.read() == originals[item["filename"]]
        assert item["hash"] == hashes[item["filename"]], \
            "a reproduced merge must bless back to the SAME canonical hash"
        assert mvcommon.calculate_file_hash(path) == item["hash"]
        assert item["status"] == "restored_local" and item["uploaded"] is True
        # .get() (not [...]) so a regression that never blesses fails with a
        # readable assertion instead of a KeyError.
        info = item.get("split_info", {})
        assert item.get("re_hashed") is True, "the first restore must bless the merged hash"
        assert info.get("merge_seed") == item["short_id"]
        assert "mkvmerge" in info.get("merge_tool", "")
        assert info.get("rehashed_at", "").endswith("Z")

    extras_dir = sandbox_extras["extras_dir"]
    assert not (extras_dir / main.RESTORE_DIR_NAME).exists(), "staged chunks consumed"
    leftovers = sorted(p.name for p in extras_dir.iterdir()
                       if ".merge_tmp" in p.name or p.name == main.TXN_JOURNAL_NAME)
    assert leftovers == []


def test_split_restore_quarantines_a_corrupt_chunk_and_keeps_the_dummy(
        sandbox_extras, mock_device, fake_dummy, plenty_of_disk, monkeypatch):
    """(d) A staged chunk whose hash does not match `split_info` is QUARANTINED
    under `restore/quarantine/`, the merge never runs, the clean chunk is left in
    `restore/` for a targeted re-fetch, and the archived dummy at the target is
    untouched (IMP-R6: never zero bytes at the path)."""
    title_id = sandbox_extras["title_id"]
    group_rel = sandbox_extras["group_rel"]

    _install_fake_split(monkeypatch, n_chunks=2)
    merges = _install_concat_merge(monkeypatch)

    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("COUNT", "2")) is True
    assert main.replace_title_extras(library, title_id) is True

    on_device = _device_names(mock_device)
    library = mvcommon.load_library()
    item = _by_sub_rel(library, title_id)["BTS.mkv"]
    target = sandbox_extras["items"][0]["path"]
    bad, good = [c["filename"] for c in item["split_info"]["chunks"]]

    restore_dir = _stage_for_restore(target.parent, {good: on_device[good]})
    with open(os.path.join(restore_dir, bad), "wb") as f:
        f.write(b"CORRUPTED-CHUNK-BYTES")

    assert main.restore_one_extra(library, title_id, group_rel, item) is False

    assert merges == [], "a bad chunk must be caught BEFORE the merge"
    quarantine_dir = os.path.join(restore_dir, "quarantine")
    assert os.path.isdir(quarantine_dir), "the corrupt chunk must be quarantined"
    quarantined = os.listdir(quarantine_dir)
    assert len(quarantined) == 1 and quarantined[0].startswith(bad)
    assert os.path.exists(os.path.join(restore_dir, good)), \
        "clean chunks stay staged for a targeted re-fetch"
    assert not os.path.exists(os.path.join(restore_dir, bad))
    assert target.read_bytes() == FAKE_DUMMY_BYTES, "the dummy must survive untouched"
    assert _by_sub_rel(mvcommon.load_library(), title_id)["BTS.mkv"]["status"] == "archived"


# ===========================================================================
# (e) parse_extras_tokens — the locked Card B1 CLI surface
# ===========================================================================


def test_parse_extras_tokens_locked_forms():
    """(e) The locked forms: `--extras` is repeatable and `;`-splittable,
    `--extras-size none` -> the ('NONE', None) sentinel, `9900mb` ->
    ('SIZE_MB', '9900'), and an empty token list yields the defaults."""
    assert main.parse_extras_tokens(
        ["--extras", "A;B", "--extras", "C", "--extras-size", "9900mb"]) == \
        {"folders": ["A", "B", "C"], "extras_size": ("SIZE_MB", "9900")}
    assert main.parse_extras_tokens(["--extras-size", "none"])["extras_size"] == \
        ("NONE", None)
    assert main.parse_extras_tokens([]) == {"folders": [], "extras_size": None}


def test_parse_extras_tokens_single_dash_aliases_and_triplet_form():
    """(e) The single-dash aliases parse identically, and the
    `SIZE_MB|SIZE_GB|COUNT <val>` triplet form consumes TWO tokens
    (case-insensitively) instead of a unit string."""
    assert main.parse_extras_tokens(["-extras", "X", "-extras-size", "4gb"]) == \
        {"folders": ["X"], "extras_size": ("SIZE_GB", "4")}
    assert main.parse_extras_tokens(["--extras-size", "SIZE_MB", "9900"])["extras_size"] == \
        ("SIZE_MB", "9900")
    assert main.parse_extras_tokens(["--extras-size", "count", "3"])["extras_size"] == \
        ("COUNT", "3")


def test_parse_extras_tokens_strips_quotes_and_ignores_unknown_tokens():
    """(e) Folder values are stripped of surrounding quotes/whitespace, empty
    fragments are dropped, and non-extras tokens (the surrounding command line)
    pass through untouched."""
    result = main.parse_extras_tokens(
        ["push", "mov-en-2024-x", "--extras", ' "C:/A" ; C:/B ; ', "device", "pixel"])
    assert result == {"folders": ["C:/A", "C:/B"], "extras_size": None}


@pytest.mark.parametrize("tokens", [
    ["--extras"],                      # flag with no value
    ["--extras-size"],                 # flag with no value
    ["--extras-size", "SIZE_MB"],      # triplet missing its number
    ["--extras-size", "banana"],       # unrecognized value
])
def test_parse_extras_tokens_fails_fast_on_a_bad_value(tokens):
    """(e) Every malformed value-taking form exits(1) rather than silently
    guessing a chunk size."""
    with pytest.raises(SystemExit) as excinfo:
        main.parse_extras_tokens(tokens)
    assert excinfo.value.code == 1


# ===========================================================================
# (f) add_extras — the one-shot lifecycle on an ARCHIVED-main title
# ===========================================================================


def test_add_extras_on_an_archived_main_title_processes_only_extras(
        sandbox_extras, mock_device, fake_dummy, stub_tech_specs, make_video):
    """(f) The whole point of `add_extras`: on a title whose MAIN content is
    already archived (a dummy on disk), the full extras lifecycle runs
    (scan+merge -> push -> dummy) while EVERY non-extras key of the title entry
    stays byte-for-byte identical, the main dummy is never re-uploaded, and only
    extras reach the device."""
    title_id = sandbox_extras["title_id"]
    main_before = _archive_main_content(sandbox_extras)

    trailers = sandbox_extras["media_dir"] / "Trailers"
    trailers.mkdir()
    make_video(trailers / "Teaser.mkv", marker=b"TEASER\n")

    main.cmd_add_extras(title_id, [str(trailers)], extras_size=("NONE", None))

    entry = mvcommon.load_library()[title_id]
    assert {k: v for k, v in entry.items() if k != "extras"} == main_before, \
        "add_extras must not touch a single non-extras field of the title entry"
    assert sandbox_extras["orig_path"].read_bytes() == FAKE_DUMMY_BYTES

    groups = entry["extras"]["groups"]
    assert sorted(groups) == ["Specials", "Trailers"]
    all_items = [it for g in groups.values() for it in g["items"]]
    assert len(all_items) == 3
    for it in all_items:
        assert it["uploaded"] is True and it["status"] == "archived"
    for _group, path, _item in main._extras_item_paths(entry):
        assert os.path.getsize(path) < main.DUMMY_MAX_BYTES

    assert set(_device_names(mock_device)) == {_remote_name(it) for it in all_items}, \
        "only the extras (never the archived main dummy) may reach the device"


def test_add_extras_no_replace_uploads_without_dummying(
        sandbox_extras, mock_device, fake_dummy, stub_tech_specs):
    """(f) `no-replace` stops after the upload: items end up onboarded/uploaded
    but keep their real bytes on disk. (Re-running against the pre-existing
    `Specials` group also proves the merge stays a no-op.)"""
    title_id = sandbox_extras["title_id"]
    _archive_main_content(sandbox_extras)

    main.cmd_add_extras(title_id, [str(sandbox_extras["extras_dir"])],
                        extras_size=("NONE", None), no_replace=True)

    groups = mvcommon.load_library()[title_id]["extras"]["groups"]
    assert sorted(groups) == ["Specials"], "no new group for a re-scan of the same folder"
    for it in _items(mvcommon.load_library(), title_id):
        assert it["uploaded"] is True and it["status"] == "onboarded"
    for it in sandbox_extras["items"]:
        assert os.path.getsize(it["path"]) > main.DUMMY_MAX_BYTES


def test_add_extras_on_an_unknown_id_is_a_clean_refusal(sandbox_extras, capsys):
    """(f) An unresolvable id is refused before any scan/push/replace — the
    library is left exactly as it was."""
    before = copy.deepcopy(mvcommon.load_library())
    # A sandbox path that does not exist — the refusal happens before any scan,
    # and nothing outside tmp_path is ever named.
    never_scanned = str(sandbox_extras["media_dir"] / "NoSuchFolder")

    main.cmd_add_extras("mov-en-2024-nosuchtitle", [never_scanned])

    assert "not found" in capsys.readouterr().out
    assert mvcommon.load_library() == before


# ===========================================================================
# (g) rename_folder — extras paths are RELATIVE, so nothing to rewrite
# ===========================================================================

RENAMED = "TestMovie {tmdb-4242}"


def test_rename_folder_keeps_the_extras_block_valid(sandbox_extras):
    """(g) extras store only RELATIVE paths (`group_rel`/`sub_rel`), so a
    cascading folder rename needs NO extras change: the block is byte-for-byte
    identical afterwards, and every canonically composed path
    (`_extras_item_paths`) resolves under the NEW folder with unchanged bytes."""
    title_id = sandbox_extras["title_id"]
    before = copy.deepcopy(mvcommon.load_library()[title_id]["extras"])
    new_folder = sandbox_extras["media_dir"].parent / RENAMED

    assert main.cmd_rename_folder(title_id, RENAMED) is True

    reloaded = mvcommon.load_library()
    assert reloaded[title_id]["folder_path"] == str(new_folder)
    assert reloaded[title_id]["extras"] == before
    assert not sandbox_extras["extras_dir"].exists()

    seen = []
    for _group, path, item in main._extras_item_paths(reloaded[title_id]):
        assert os.path.isfile(path), f"extra lost by the rename: {path}"
        assert mvcommon.calculate_file_hash(path) == item["hash"]
        seen.append(os.path.relpath(path, new_folder).replace(os.sep, "/"))
    assert seen == ["Specials/BTS.mkv", "Specials/Trailer.mkv"]


# ===========================================================================
# (h) AUTOPILOTS — the one-shot promise on a BRAND-NEW title (Step 12b)
#
# These start from the BARE `sandbox` (not `sandbox_extras`): the title must NOT
# already exist, because what is under test is whether the autopilots' PREP LEG
# registers the `--extras` folders at all. Step 12's doc audit found that
# `cmd_prep_push_rep` called `cmd_prep(manual_id, filepath)` and
# `cmd_prep_push_rep_season` called `cmd_prep_season(base_id, folder_path)` WITHOUT
# forwarding `extras`, so on a fresh title nothing was registered and the trailing
# `push_title_extras` / `replace_title_extras` pair silently no-opped (an empty
# `groups` dict returns success). Same proven recipe as the smoke autopilot cases
# (tests/smoke/test_smoke_all_commands.py::test_prep_push_rep): whole-file pushes
# need no `plenty_of_disk` because nothing splits.
# ===========================================================================


def test_movie_autopilot_registers_pushes_and_dummies_extras_on_a_fresh_title(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy):
    """(h) `prep_push_rep <id> <file> --extras "<Specials>"` on a brand-new title is
    a TRUE ONE-SHOT — no prior `prep --extras` / `add_extras` needed: the prep leg
    REGISTERS the folder (scan+hash+merge), the extras phase UPLOADS it, and the
    replace phase DUMMIES it, all in the same run. Asserts the whole promise on
    observable state (library block, bytes on the fake device, bytes on disk) plus
    the main content's own unchanged autopilot outcome, and pins the exact device
    contents so a stray upload cannot hide."""
    title_id = "mov-en-2024-autoextras"
    media_dir = sandbox["media_dir"]
    main_path = media_dir / "Auto.mkv"
    _, main_hash = make_video(main_path, marker=b"AUTO-MAIN\n")

    extras_dir = media_dir / "Specials"
    extras_dir.mkdir()
    originals, hashes = {}, {}
    for fn, marker in [("BTS.mkv", b"AUTO-BTS\n"), ("Teaser.mkv", b"AUTO-TEASER\n")]:
        _, hashes[fn] = make_video(extras_dir / fn, marker=marker)
        originals[fn] = (extras_dir / fn).read_bytes()

    main.cmd_prep_push_rep(title_id, str(main_path), extras=[str(extras_dir)],
                           extras_size=("NONE", None))

    entry = mvcommon.load_library()[title_id]

    # (i) REGISTERED by the prep leg — the group exists ONLY if --extras was forwarded.
    groups = entry.get("extras", {}).get("groups", {})
    assert sorted(groups) == ["Specials"], \
        f"the prep leg did not register the extras: {entry.get('extras')!r}"
    items = groups["Specials"]["items"]
    assert [it["sub_rel"] for it in items] == ["BTS.mkv", "Teaser.mkv"]
    for it in items:
        assert it["hash"] == hashes[it["filename"]], \
            "the registered hash must be the REAL pre-dummy sha256 of the extra"

    # (ii) PUSHED — the REAL (pre-dummy) bytes are on the device, under a remote
    # folder mirroring Specials/, and NOTHING beyond the main file + the 2 extras.
    on_device = _device_names(mock_device)
    for it in items:
        remote = _remote_name(it)
        assert remote in on_device, f"{remote} not on device: {sorted(on_device)}"
        assert on_device[remote].read_bytes() == originals[it["filename"]]
        assert on_device[remote].parent.name == "Specials"
    # _remote_name works on the title entry too (same filename/short_id keys, and
    # cmd_push builds the whole-file remote name identically, main.py:4574).
    assert set(on_device) == {_remote_name(entry)} | {_remote_name(it) for it in items}

    # (iii) DUMMIED — every extra archived, a tiny dummy at its canonical path.
    for _group, path, it in main._extras_item_paths(entry):
        assert it["uploaded"] is True and it["status"] == "archived"
        with open(path, "rb") as f:
            assert f.read() == FAKE_DUMMY_BYTES

    # The autopilot's MAIN promise is unchanged by the forwarding.
    assert entry["status"] == "archived" and entry["uploaded"] is True
    assert entry["hash"] == main_hash
    assert main_path.read_bytes() == FAKE_DUMMY_BYTES


def test_season_autopilot_registers_pushes_and_dummies_the_seasons_extras(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy):
    """(h) The season variant of the same one-shot promise: the extras land on the
    SEASON_MAP title (Card A2 — never on an episode leaf), are uploaded after the
    episode loop and dummied, while every episode still ends `archived`. Also pins
    that the scan happens EXACTLY ONCE per run: `cmd_prep_season` merges after its
    episode loop and never passes `extras` down to the per-episode `cmd_prep`, so a
    single group with a single item is the correct end state."""
    base_id = "tv-en-2021-autoextras-s01"
    season_dir = sandbox["local_root"] / "Series" / "AutoExtras S01"
    season_dir.mkdir(parents=True)
    for n in (1, 2):
        make_video(season_dir / f"AUTO.S01E0{n}.mkv", marker=f"ep{n}".encode())

    extras_dir = season_dir / "Specials"
    extras_dir.mkdir()
    bts = extras_dir / "BTS.mkv"
    _, bts_hash = make_video(bts, marker=b"SEASON-BTS\n")
    bts_original = bts.read_bytes()

    main.cmd_prep_push_rep_season(base_id, str(season_dir),
                                  extras=[str(extras_dir)],
                                  extras_size=("NONE", None))

    library = mvcommon.load_library()
    season = library[base_id]
    assert season["type"] == "season_map"

    # (i) REGISTERED on the season_map by the prep_season leg.
    groups = season.get("extras", {}).get("groups", {})
    assert sorted(groups) == ["Specials"], \
        f"the prep_season leg did not register the extras: {season.get('extras')!r}"
    item, = groups["Specials"]["items"]      # exactly one item -> exactly one scan
    assert item["sub_rel"] == "BTS.mkv" and item["hash"] == bts_hash

    # Every episode is archived as before, and carries NO extras block of its own.
    assert season["children"] == [f"{base_id}e01", f"{base_id}e02"]
    for ep_id in season["children"]:
        assert library[ep_id]["status"] == "archived"
        assert "extras" not in library[ep_id]

    # (ii) PUSHED — real bytes, remote folder mirrors Specials/.
    on_device = _device_names(mock_device)
    remote = _remote_name(item)
    assert remote in on_device, f"{remote} not on device: {sorted(on_device)}"
    assert on_device[remote].read_bytes() == bts_original
    assert on_device[remote].parent.name == "Specials"

    # (iii) DUMMIED.
    assert item["uploaded"] is True and item["status"] == "archived"
    assert bts.read_bytes() == FAKE_DUMMY_BYTES


def test_autopilot_without_extras_leaves_bonus_content_completely_alone(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy):
    """(h) The no-flag path is byte-for-byte the pre-Step-12b behavior: the
    forwarding is FLAG-GATED (`cmd_prep`'s scan sits inside `if extras:`), so a
    plain `prep_push_rep` run leaves a `Specials/` folder sitting next to the movie
    completely untouched — no `extras` key on the entry, nothing extra on the
    device, and the bonus files keep their real bytes."""
    title_id = "mov-en-2024-noextras"
    media_dir = sandbox["media_dir"]
    main_path = media_dir / "Plain.mkv"
    make_video(main_path, marker=b"PLAIN-MAIN\n")

    extras_dir = media_dir / "Specials"
    extras_dir.mkdir()
    bonus = extras_dir / "BTS.mkv"
    make_video(bonus, marker=b"UNREGISTERED-BTS\n")
    bonus_original = bonus.read_bytes()

    main.cmd_prep_push_rep(title_id, str(main_path))

    entry = mvcommon.load_library()[title_id]
    assert "extras" not in entry, "a no-flag autopilot run must not register anything"
    assert entry["status"] == "archived"
    assert set(_device_names(mock_device)) == {_remote_name(entry)}, \
        "only the main content may reach the device without --extras"
    assert bonus.read_bytes() == bonus_original
    assert os.path.getsize(bonus) > main.DUMMY_MAX_BYTES, "the bonus file must NOT be dummied"


def test_extras_push_still_works_after_a_folder_rename(sandbox_extras, mock_device):
    """(g) Operational proof: pushing AFTER a rename resolves the extras from the
    rewritten title `folder_path` and mirrors the NEW folder name into the remote
    path — the extras lifecycle survives an IMP-D17 rename end to end."""
    title_id = sandbox_extras["title_id"]
    assert main.cmd_rename_folder(title_id, RENAMED) is True

    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("NONE", None)) is True

    on_device = _device_names(mock_device)
    assert len(on_device) == 2
    for path in on_device.values():
        assert path.parent.name == "Specials"
        assert path.parent.parent.name == RENAMED
    for it in _items(mvcommon.load_library(), title_id):
        assert it["uploaded"] is True and it["status"] == "onboarded"


# ===========================================================================
# (i) D19-B1 LAYERED GUARD (Step 12c) — a re-scan over an archived group is SAFE
#
# The hazard (STATUS.md Step 12b §5): scan_extras_folders has no dummy-size
# filter, so re-scanning an ALREADY-ARCHIVED group (re-run add_extras /
# prep_season --extras + push_group --extras / the season autopilot) scans the
# tiny dummies replace left on disk; pre-guard, merge_extras_into_title's
# changed-hash branch then reset each item to local_ready/uploaded=False with
# the DUMMY's hash (dropping split_info/re_hashed) and the next push uploaded
# the dummy OVER the real cloud copy — unrecoverable, the real local bytes were
# already reclaimed. Layer 1 (merge) keeps cloud-bearing items as stored when
# the changed-hash candidate is dummy-sized (and refuses to register NEW
# dummy-sized files); layer 2 (push_one_extra) refuses to upload any
# dummy-sized file, whatever the caller.
# ===========================================================================


def test_rescan_over_an_archived_group_cannot_clobber_cloud_bearing_items(
        sandbox_extras, mock_device, fake_dummy, plenty_of_disk,
        stub_tech_specs, monkeypatch, capsys):
    """(i) THE D19-B1 regression, at the core seam: split-push + replace an
    extras group (dummies on disk, items archived/uploaded with the REAL hashes,
    split_info and a re_hashed stamp), then re-scan+merge over the group. Every
    stored field must survive byte-for-byte (hash / split_info / re_hashed /
    status / uploaded), a protect-warning must name each file, and a follow-up
    push must upload NOTHING (the device is bit-identical, sidecars included)."""
    title_id = sandbox_extras["title_id"]
    _install_fake_split(monkeypatch, n_chunks=2)

    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("COUNT", "2")) is True
    assert main.replace_title_extras(library, title_id) is True
    # Stamp re_hashed as a restore-bless would (same shortcut as the (a)
    # changed-bytes test), so the equality below also proves the guard keeps it.
    library = mvcommon.load_library()
    for it in _items(library, title_id):
        it["re_hashed"] = True
    mvcommon.save_library(library)

    before = copy.deepcopy(mvcommon.load_library()[title_id]["extras"])
    real_hashes = {it["filename"]: it["hash"] for it in sandbox_extras["items"]}
    device_before = {str(p): p.read_bytes()
                     for p in mock_device.rglob("*") if p.is_file()}
    for it in sandbox_extras["items"]:      # precondition: dummies on disk
        assert it["path"].read_bytes() == FAKE_DUMMY_BYTES

    library = mvcommon.load_library()
    main.merge_extras_into_title(library, title_id, main.scan_extras_folders(
        [str(sandbox_extras["extras_dir"])], str(sandbox_extras["media_dir"]),
        title_id))
    mvcommon.save_library(library)

    out = capsys.readouterr().out
    assert "refusing to clobber" in out
    assert "Specials/BTS.mkv" in out and "Specials/Trailer.mkv" in out

    assert mvcommon.load_library()[title_id]["extras"] == before, \
        "a dummy-sized re-scan must not change ANY stored field of the group"
    for it in _items(mvcommon.load_library(), title_id):   # readable spot-checks
        assert it["hash"] == real_hashes[it["filename"]]
        assert it["status"] == "archived" and it["uploaded"] is True
        assert it["split_info"]["is_split"] is True and it["re_hashed"] is True

    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("NONE", None)) is True
    assert {str(p): p.read_bytes()
            for p in mock_device.rglob("*") if p.is_file()} == device_before, \
        "nothing may be uploaded after a protected re-scan"


def test_add_extras_rerun_over_an_archived_group_is_safe(
        sandbox_extras, mock_device, fake_dummy, stub_tech_specs, capsys):
    """(i) The documented user-facing entry to the D19-B1 hazard: re-running
    `add_extras <id> "<Specials>"` after the group was archived. The re-scan is
    protected (items stay archived with the REAL hashes), nothing new reaches
    the device, and the dummies on disk stay dummies — the re-run is a safe,
    warned no-op instead of a data-loss path."""
    title_id = sandbox_extras["title_id"]
    main.cmd_add_extras(title_id, [str(sandbox_extras["extras_dir"])],
                        extras_size=("NONE", None))
    for it in _items(mvcommon.load_library(), title_id):
        assert it["status"] == "archived" and it["uploaded"] is True

    before = copy.deepcopy(mvcommon.load_library()[title_id]["extras"])
    device_before = {str(p): p.read_bytes()
                     for p in mock_device.rglob("*") if p.is_file()}
    capsys.readouterr()  # drop the first run's output

    main.cmd_add_extras(title_id, [str(sandbox_extras["extras_dir"])],
                        extras_size=("NONE", None))

    out = capsys.readouterr().out
    assert "refusing to clobber" in out
    assert mvcommon.load_library()[title_id]["extras"] == before
    assert {str(p): p.read_bytes()
            for p in mock_device.rglob("*") if p.is_file()} == device_before
    for it in sandbox_extras["items"]:
        assert it["path"].read_bytes() == FAKE_DUMMY_BYTES, \
            "the dummies must stay dummies (no re-upload, no re-dummy)"


def test_push_refuses_a_dummy_sized_extra_and_continues_with_the_rest(
        sandbox_extras, mock_device, capsys):
    """(i) Layer-2 backstop in isolation: an item still local_ready whose
    on-disk file is dummy-sized is REFUSED (loud, with the DUMMY_MAX_BYTES
    threshold named) and no byte of it reaches the device; the batch continues —
    its real-sized sibling uploads — and reports False, exactly the existing
    missing-file failure semantics."""
    title_id = sandbox_extras["title_id"]
    bts, trailer = sandbox_extras["items"]
    bts["path"].write_bytes(FAKE_DUMMY_BYTES)   # dummy-sized; item stays local_ready

    library = mvcommon.load_library()
    assert main.push_title_extras(library, title_id, ("NONE", None)) is False

    out = capsys.readouterr().out
    assert "Refusing to push extra BTS.mkv" in out
    assert f"{main.DUMMY_MAX_BYTES:,} B" in out, \
        "the refusal must state the actual DUMMY_MAX_BYTES threshold"

    after = _by_sub_rel(mvcommon.load_library(), title_id)
    assert after["BTS.mkv"]["uploaded"] is False
    assert after["BTS.mkv"]["status"] == "local_ready"
    assert after["Trailer.mkv"]["uploaded"] is True
    assert after["Trailer.mkv"]["status"] == "onboarded"
    assert set(_device_names(mock_device)) == {_remote_name(trailer)}, \
        "no byte of the dummy-sized extra may reach the device"
    assert len(list(mock_device.rglob("*.mvmeta.json"))) == 1, \
        "no sidecar for the refused extra either"


def test_merge_does_not_register_a_new_dummy_sized_file(
        sandbox_extras, stub_tech_specs, capsys):
    """(i) The NEW-item path of the layer-1 guard: a dummy-sized file whose
    sub_rel is NOT yet registered (e.g. `--extras` pointed at a folder of
    another title's reclaim dummies) is skipped with a warning instead of
    registered — registering it would only create an item layer 2 refuses on
    every push. The real-sized siblings' idempotent no-op is untouched."""
    title_id = sandbox_extras["title_id"]
    tiny = sandbox_extras["extras_dir"] / "Tiny.mkv"
    tiny.write_bytes(FAKE_DUMMY_BYTES)

    library = mvcommon.load_library()
    before = copy.deepcopy(library[title_id]["extras"])
    main.merge_extras_into_title(library, title_id, main.scan_extras_folders(
        [str(sandbox_extras["extras_dir"])], str(sandbox_extras["media_dir"]),
        title_id))

    out = capsys.readouterr().out
    assert "Skipping extras file Specials/Tiny.mkv" in out
    assert library[title_id]["extras"] == before, \
        "the dummy-sized file must not be registered; siblings stay a no-op"
    assert "Tiny.mkv" not in _by_sub_rel(library, title_id)
