"""IMP-C25 Step 15 — the cross-command REGRESSION PINS (docs/feature-fetch-datetime/PLAN.md Step 15).

Changing anything pinned here is a REVIEWED decision (re-pin it on purpose, in the same change), never a side effect.

1. THE REGISTRY — the pins PLAN Step 15 names that already exist in the focused modules. They are NOT re-asserted
   here (no duplicates); this file only proves that each still exists, so a deleted or renamed pin fails the suite
   instead of vanishing silently:
     parse_fetch_args shape        test_cli_parsers.py::TestParseFetchArgs (the 4-tuple, every exit) ·
                                   test_fetch_routing.py: the 4-tuple with --manual present; parse_fetch_options' shape
     spawned fetch argv            test_fetch_child_stream.py: argv + env + Popen kwargs; every mainfetch child spawn
                                   gets the child env · test_fetch_routing.py: --manual forwarded only when given; the
                                   web UI never passes it · the other children: test_check_photos.py (the check child)
                                   and test_auto_confirm.py (the auto-confirm child — exactly its argv; RE-PINNED on
                                   purpose 2026-10-27, IMP-C25 acfix: `--min-age-h AUTO_CONFIRM_MIN_AGE_H` added)
     auto-confirm: old objects     (IMP-C25 acfix — the user's finding, DECISIONS.md §1g) test_auto_confirm.py: the
     only                          auto-confirm's check hands only objects pushed >= AUTO_CONFIRM_MIN_AGE_H ago to the
                                   browser child · test_check_photos.py: `--min-age-h` never looks a younger object up
                                   nor records anything for it; it is part of the resume key
     web markers (P-13)            test_fetch_child_stream.py: today's frozen transcripts keep their progress counts
     trigger_download digest       test_fetch_search_wait.py: trigger_download + the search wait it relies on
     calculate_file_hash           test_gp_capture.py (SHA-256 for 0 / 10 / 196 615 bytes + the same-pass SHA-1) ·
                                   test_mvcommon.py (the progress bar; a missing file -> None)
     ENTRY_TYPE_KEYS literal       test_entry_schema_guard.py (d1)
     mvmeta v1 keys                test_gp_capture.py (the v1 key set and values inside v2; only the known v2 keys) ·
                                   test_mvmeta_upgrade.py (MVMETA_KEYS / MVMETA_UPGRADE_KEYS / the version drift pin)
     gprpc.ALLOWED_RPCIDS literal  test_gprpc.py (== the literal P12 set, disjoint from the P13 mutating ids)

2. THE MISSING PINS, added below:
     - fetch_restore (also the web UI's fetch_restore action, which calls it in-process) spawns EXACTLY the fetch
       child argv of `fetch` — the existing pins drive cmd_dispatch_fetch only;
     - P-13 literally, on both sides: the exact source of every string literal that carries a web-progress marker in
       the fetch child's modules and in main.py (a NEW marker line anywhere fails here — P-13 "no new line may contain
       those markers"), and the exact patterns webui.server's progress parser scans for;
     - calculate_file_hash's return contract beyond the existing pins: its signature, a lowercase 64-hex str, and None
       — never an exception — when the file cannot be read;
     - the mvmeta v1 document's key ORDER and the writer's exact byte form (json.dump indent=2, ASCII) — the form
       mvmeta_upgrade's byte-identity rule requires before it rewrites a phone sidecar (IMP-C25 Step 13c).

Hermetic: no adb, no Chrome, no network; files only under tmp_path (the conftest autouse guards apply).
"""
import ast
import functools
import hashlib
import inspect
import json
import os
import re
import types

import pytest

import main
import mvcommon

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS)


# ================================================================================ 1. the registry of existing pins

EXISTING_PINS = [
    # parse_fetch_args shape
    ("test_cli_parsers.py", "TestParseFetchArgs::test_id_only_returns_mid_and_none"),
    ("test_cli_parsers.py", "TestParseFetchArgs::test_tempdir_is_parsed"),
    ("test_cli_parsers.py", "TestParseFetchArgs::test_fetchExtras_aliases_and_order_independent"),
    ("test_cli_parsers.py", "TestParseFetchArgs::test_fetch_with_no_id_raises_system_exit"),
    ("test_fetch_routing.py", "test_parse_fetch_args_keeps_its_four_tuple_with_manual_present"),
    ("test_fetch_routing.py", "test_parse_fetch_options_reads_manual_anywhere_after_the_id"),
    # spawned fetch argv (and the other children's)
    ("test_fetch_child_stream.py", "test_the_fetch_child_runs_unbuffered_and_in_utf8_and_inherits_the_rest"),
    ("test_fetch_child_stream.py", "test_every_mainfetch_child_spawn_uses_the_child_env"),
    ("test_fetch_routing.py", "test_the_fetch_child_gets_manual_only_when_given"),
    ("test_fetch_routing.py", "test_the_main_command_line_forwards_manual_only_when_given"),
    ("test_fetch_routing.py", "test_the_web_ui_never_passes_manual"),
    ("test_check_photos.py", "test_a_check_creates_its_run_directory_and_hands_it_to_the_child"),
    ("test_auto_confirm.py", "test_an_old_pending_push_runs_the_exact_check_child_through_the_checks_spawner"),
    # the auto-confirm looks up only objects old enough (IMP-C25 acfix, 2026-10-27)
    ("test_auto_confirm.py", "test_the_auto_confirm_child_looks_up_only_the_old_objects_never_the_push_that_just_ran"),
    ("test_check_photos.py", "test_min_age_h_looks_up_only_the_old_objects_and_records_nothing_for_the_young_ones"),
    ("test_check_photos.py", "test_min_age_h_is_part_of_the_resume_key"),
    # web markers (P-13): the consumer's counts on today's frozen transcripts
    ("test_fetch_child_stream.py", "test_the_web_progress_counts_of_todays_transcripts_are_unchanged"),
    # trigger_download source digest
    ("test_fetch_search_wait.py", "test_trigger_download_source_is_pinned"),
    ("test_fetch_search_wait.py", "test_the_search_wait_it_relies_on_is_pinned"),
    # calculate_file_hash SHA-256 / return contract
    ("test_gp_capture.py", "test_calculate_file_hash_sha256_unchanged_and_sha1_cached"),
    ("test_mvcommon.py", "test_calculate_file_hash_progress_bar"),
    ("test_mvcommon.py", "test_calculate_file_hash_missing_file"),
    # ENTRY_TYPE_KEYS literal
    ("test_entry_schema_guard.py", "test_gphotos_adds_no_entry_type_and_no_required_key"),
    # mvmeta v1 keys
    ("test_gp_capture.py", "test_split_push_writes_mvmeta_v2_with_every_v1_key_unchanged"),
    ("test_gp_capture.py", "test_write_remote_mvmeta_adds_only_the_known_v2_keys"),
    ("test_mvmeta_upgrade.py", "test_the_upgrade_writes_today_s_v2_keys_plus_the_account"),
    # gprpc.ALLOWED_RPCIDS literal
    ("test_gprpc.py", "test_the_allowlist_is_exactly_the_read_only_p12_ids"),
]


@functools.lru_cache(maxsize=None)
def _source(name):
    with open(os.path.join(TESTS, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("module, pin", EXISTING_PINS, ids=[f"{m}::{p}" for m, p in EXISTING_PINS])
def test_the_pin_still_exists(module, pin):
    text = _source(module)
    *owner, name = pin.split("::")
    start = 0
    if owner:                                  # a test method: it must sit inside its class
        found = re.search(rf"^class {re.escape(owner[0])}\b", text, re.M)
        assert found, f"{module}: class {owner[0]} is gone — the pin it held must be re-homed on purpose"
        start = found.end()
    assert re.search(rf"^\s*def {re.escape(name)}\(", text[start:], re.M), (
        f"{module}: the pin {pin} is gone — a pinned contract may only change as a reviewed decision")


# ================================================================================ 2a. the spawned fetch argv

class _Child:
    stdout = []

    def wait(self):
        return 0


@pytest.mark.parametrize("kwargs, argv", [
    ({}, ["python", "mainfetch.py", "fetch", "tv-x"]),
    ({"episode_range": "1-3"}, ["python", "mainfetch.py", "fetch", "tv-x", "episodes", "1-3"]),
    ({"fetch_extras": True, "temp_dir": "D:\\t"},
     ["python", "mainfetch.py", "fetch", "tv-x", "--fetchExtras", "tempdir", "D:\\t"]),
    ({"manual": True}, ["python", "mainfetch.py", "fetch", "tv-x", "--manual"]),
], ids=["bare", "episodes", "extras-tempdir", "manual"])
def test_fetch_restore_spawns_exactly_the_fetch_child_argv(sandbox, monkeypatch, capsys, kwargs, argv):
    spawned = []
    monkeypatch.setattr(main.subprocess, "Popen", lambda cmd, **k: spawned.append(list(cmd)) or _Child())
    monkeypatch.setattr(main, "merge_learned_gphotos", lambda: None)
    episode_range = kwargs.get("episode_range")
    rest = {k: v for k, v in kwargs.items() if k != "episode_range"}

    main.cmd_dispatch_fetch("tv-x", episode_range, **rest)
    main.cmd_fetch_restore("tv-x", episode_range, **rest)        # the web UI's fetch_restore action calls this

    assert spawned == [argv, argv]


# ================================================================================ 2b. P-13 web markers, literally

P13_MARKERS = ("🔹 PROCESSING:", "Detected Split File", "✅ MOVED:", "✅ ENTRY COMPLETE.")
# The exact source of every string literal (plain or f-string, docstrings excluded) that carries a marker — today's.
# mainfetch.py: the fetch child's four progress lines; main.py: cmd_restore's two split lines (the restore phase of a
# web fetch_restore job is parsed too); the other modules a fetch child runs print none.
P13_SITES = {
    "mainfetch.py": sorted([
        "f\"     ✅ MOVED: {matched['filename']}\"",
        "f\"\\n🔹 PROCESSING: {entry['filename']} ({entry.get('short_id', 'N/A')})\"",
        "f\"   > Detected Split File ({entry['split_info']['total_chunks']} chunks)\"",
        "\"\\n   ✅ ENTRY COMPLETE.\"",
    ]),
    "main.py": sorted([
        "f\"   > {label} Detected Split File ({len(chunks)} chunks).\"",
        "f\"   > Detected Split File ({entry['split_info']['total_chunks']} chunks).\"",
    ]),
    "gprpc.py": [], "gpweb.py": [], "mvcommon.py": [], "gpcapture.py": [],
}


def _marker_literals(path):
    """The source segment of every string literal in `path` holding a P-13 marker (docstrings excluded), sorted."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                skip.add(id(first.value))                    # a docstring describes; it never prints
        if isinstance(node, ast.JoinedStr):
            skip.update(id(part) for part in node.values)    # an f-string is judged as a whole
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            text = "".join(p.value for p in node.values if isinstance(p, ast.Constant))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            text = node.value
        else:
            continue
        if any(marker in text for marker in P13_MARKERS):
            found.append(ast.get_source_segment(src, node))
    return sorted(found)


@pytest.mark.parametrize("module", sorted(P13_SITES))
def test_the_p13_markers_are_printed_only_where_they_always_were(module):
    assert _marker_literals(os.path.join(REPO, module)) == P13_SITES[module], (
        f"{module}: a line carrying a web-progress marker was added or changed — PLAN P-13: the markers are printed "
        f"exactly as before and no new line may contain them (webui.server._parse_progress counts them)")


def test_the_web_progress_parser_scans_exactly_these_markers():
    web = pytest.importorskip("webui.server")
    assert (web._RE_PROCESSING.pattern, web._RE_SPLIT.pattern, web._RE_MOVED.pattern) == (
        r"^🔹 PROCESSING:", r"^\s*> Detected Split File \((\d+) chunks\)", r"^\s*✅ MOVED:")
    assert all(rx.flags & re.M for rx in (web._RE_PROCESSING, web._RE_SPLIT, web._RE_MOVED))
    assert web._PROGRESS_SIGNALS == ("🔹 PROCESSING:", "Detected Split File", "✅ MOVED:")


# ================================================================================ 2c. calculate_file_hash's contract

def test_calculate_file_hash_signature_and_return_type(tmp_path, capsys):
    params = inspect.signature(mvcommon.calculate_file_hash).parameters
    assert list(params) == ["filepath", "block_size"] and params["block_size"].default == 65536
    data = b"pinned bytes " * 9000                          # crosses a block boundary (117 000 bytes)
    path = tmp_path / "f.bin"
    path.write_bytes(data)
    digest = mvcommon.calculate_file_hash(str(path))
    assert isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == hashlib.sha256(data).hexdigest()
    assert main.calculate_file_hash is mvcommon.calculate_file_hash   # main's name is the same function


def test_calculate_file_hash_returns_none_and_never_raises_when_the_file_cannot_be_read(tmp_path, capsys):
    folder = tmp_path / "not-a-file.mkv"                    # it exists, but cannot be opened for reading
    folder.mkdir()
    assert mvcommon.calculate_file_hash(str(folder)) is None
    assert "❌" in capsys.readouterr().out
    assert mvcommon.cached_sha1(str(folder)) is None        # a failed read caches no content key


# ================================================================================ 2d. the mvmeta v1 document

MVMETA_V1_ORDER = ["version", "manual_id", "short_id", "base_filename", "original_hash", "is_split", "method", "val",
                   "total_chunks", "chunks", "folder_path", "remote_target_dir", "tech_spec", "metadata"]


@pytest.mark.parametrize("split", [False, True], ids=["whole", "split"])
def test_the_mvmeta_writer_keeps_the_v1_key_order_and_its_exact_byte_form(monkeypatch, capsys, split):
    pushed = {}

    def run(argv, check=False, **kw):
        with open(argv[-2], "rb") as fh:                    # the temp sidecar, read before it is removed
            pushed[argv[-1]] = fh.read()
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(main.subprocess, "run", run)
    entry = {"short_id": "abc123", "filename": "Movie.mkv", "hash": "3" * 64, "folder_path": "X",
             "tech_spec": {"resolution": "1080p"}, "metadata": {"title": "Mövie"}}
    if split:
        entry["split_info"] = {"is_split": True, "method": "COUNT", "val": "2", "total_chunks": 2,
                               "chunks": [{"filename": "Movie [abc123].chunk.001.mkv", "hash": "1" * 64},
                                          {"filename": "Movie [abc123].chunk.002.mkv", "hash": "2" * 64}]}

    assert main.write_remote_mvmeta(["adb"], "/sdcard/Media/Movies/M", "mov-en-2020-movie", entry) is True

    [raw] = pushed.values()
    doc = json.loads(raw)
    assert list(doc) == MVMETA_V1_ORDER and doc["version"] == main.MVMETA_SCHEMA_VERSION
    assert all(list(chunk) == ["filename", "hash"] for chunk in doc["chunks"])   # no identity: v1 chunks only
    assert raw.isascii()                                                          # json.dump's ASCII escapes
    assert main._mu_decode(raw)[2] is None    # the writer's exact form: what mvmeta_upgrade may later rewrite
