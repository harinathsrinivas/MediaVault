"""IMP-C25 Step 14 — `resolve_photos`: the user resolves the failed list by hand (docs/feature-fetch-datetime/PLAN.md
Step 14 + "Follow-ups carried into Step 14" b; OD-14 ruling; P-5, P-10, P-18; the design decisions of
.candidates/c25-step14/STATUS.md).

  14.e  mvcommon's state for it: the answer log resolved.jsonl (append_resolved / read_resolved — resume, and the
        IMP-C27 "not uploaded" list), read_failed_csv_rows (every object row of a failed_objects.csv /
        resolutions_todo.csv, edited or not — read_failed_csv's Excel-tolerant reading, read_failed_csv unchanged) and
        failed_objects (the failed list against the library — export_failed_csv's rows, now shared)
  14.f  `resolve_photos [failed|<csv>] [--account a] [--id <manual_id>]` with scripted stdin: the terminal-only
        refusal; the rows (newest failed row per object; confirmed, vanished and answered ones left out; download-lost
        ones listed and offered a re-fetch); one Chrome session per account under the fetch lock; the identification
        block and the best start; paste (a matching URL -> proof dedupkey / filename, source check; another item ->
        the manual-match question; PROVEN other bytes -> refused), search (search_override + S3u at once), not
        uploaded (the IMP-C27 list), skip, quit, end of input; every answer written at once; the final dry-run summary
        and "Apply these N changes to the library? [y/N]"; quit mid-way then resume; Ctrl+C; the command line; the
        check report's ambiguous line (follow-up b)

Hermetic: the fake Google Photos world (tests/gphotos_fake.py); mainfetch's and gprpc's clocks are fake; every state
dir, ~/Downloads and the process table are redirected (tests/conftest.py); the keyboard is a scripted stdin. Synthetic
ids only (every photo id contains "FAKE").
"""
import contextlib
import copy
import hashlib
import io
import os
import sys
import types
from datetime import datetime, timezone

import pytest

import main
import mainfetch
import mvcommon
from test_check_photos import (ONE, _Beat, _only_read_only_rpcids_ever, _single, clock, cw)  # noqa: F401
from test_gphotos_primitives import (ANIME, EP_SPLIT, EP_WHOLE, MOVIE, PID, T, _csv_rows, _edit, _edited_csv,  # noqa: F401
                                     _failed, _h, lib)
from test_locator import TAKEN, _bytes, _canary_inventories, _capture, _confirmed_elsewhere, _obj, _up, _whole
from test_map_photos import _main_guard

REAL_STDIN_IS_TTY = main._rp_stdin_is_tty   # captured before any fixture replaces it


# ===========================================================================
# 14.e — the answer log resolved.jsonl
# ===========================================================================
def _answer(**kw):
    record = {"account": "tv", "manual_id": EP_WHOLE, "label": "whole", "sha256": _h(EP_WHOLE), "answer": "url",
              "photo_id": PID[0], "proof": "dedupkey"}
    record.update(kw)
    return {k: v for k, v in record.items() if v is not None}


def test_an_answer_is_one_fsynced_line_in_a_fixed_key_order(monkeypatch):
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(mvcommon.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: datetime(2026, 10, 14, 1, 2, 3, tzinfo=timezone.utc))

    written = mvcommon.append_resolved(_answer(proof="filename", label="whole"))

    assert list(written) == ["v", "at", "account", "manual_id", "label", "sha256", "answer", "photo_id", "proof"]
    assert written["at"] == "2026-10-14T01:02:03Z" and synced                  # fsynced at once
    assert mvcommon.read_resolved() == [written]
    assert mvcommon.resolved_path() == os.path.join(mvcommon.GPHOTOS_STATE_DIR, "resolved.jsonl")


@pytest.mark.parametrize("record", [
    _answer(answer="search", photo_id=None, proof=None, search_term="  silicon valley opener  ", found=False),
    _answer(answer="search", search_term="the term", found=True),             # the term found it, verified
    _answer(answer="not_uploaded", photo_id=None, proof=None),
    _answer(proof="manual"),                                                   # an accepted manual match
])
def test_every_kind_of_answer_round_trips(record):
    written = mvcommon.append_resolved(record)
    assert mvcommon.read_resolved() == [written]
    if record["answer"] == "search":
        assert written["search_term"] == record["search_term"].strip()


@pytest.mark.parametrize("record", [
    _answer(answer="maybe"), _answer(account="films"), _answer(manual_id=""), _answer(sha256="abc"),
    _answer(photo_id="not a key!"), _answer(proof="guess"), _answer(photo_id=None), _answer(proof=None),
    _answer(answer="search", photo_id=None, proof=None),                       # no search term
    _answer(answer="search", photo_id=None, proof=None, search_term="   "),
    _answer(answer="not_uploaded", proof=None),                                # carries an item
    _answer(answer="not_uploaded", photo_id=None, proof=None, search_term="x"),
    _answer(search_term="x"),                                                  # a url answer with a term
    _answer(found="yes"), _answer(colour="red"), _answer(at="yesterday"), _answer(label=5), "nope"])
def test_an_invalid_answer_is_refused_and_nothing_is_written(record):
    with pytest.raises(ValueError):
        mvcommon.append_resolved(record)
    assert not os.path.exists(mvcommon.resolved_path())


def test_the_answer_log_is_torn_line_tolerant():
    first = mvcommon.append_resolved(_answer())
    with open(mvcommon.resolved_path(), "ab") as fh:
        fh.write(b'{"account": "tv", "answer": "url"}\n{"torn')
    second = mvcommon.append_resolved(_answer(answer="not_uploaded", photo_id=None, proof=None))
    errors = []
    assert mvcommon.read_resolved(errors) == [first, second]
    assert [e["line"] for e in errors] == [2, 3]


# ===========================================================================
# 14.e — read_failed_csv_rows: every object row, edited or not
# ===========================================================================
def test_every_object_row_is_read_edited_or_not(tmp_path, lib):
    mvcommon.append_failed(_failed())
    chunk = lib[EP_SPLIT]["split_info"]["chunks"][1]
    mvcommon.append_failed(_failed(manual_id=EP_SPLIT, sha256=chunk["hash"], label="chunk 2/2", reason="ambiguous",
                                   expected_taken_local="2022-02-05 19:49:46 GMT+08:00"))
    exported = _csv_rows(mvcommon.export_failed_csv(lib)["path"])
    rows = [dict(exported[0], decision="skip"), exported[1], {c: "" for c in mvcommon.GP_FAILED_CSV_COLUMNS}]

    read, errors = mvcommon.read_failed_csv_rows(_edited_csv(tmp_path, rows))

    assert errors == []
    assert read == [
        {"line": 2, "account": "tv", "manual_id": EP_WHOLE, "category": "tv", "object": "whole",
         "uploaded_name": exported[0]["uploaded_name"], "status": "not-found", "sha256": _h(EP_WHOLE)},
        {"line": 3, "account": "tv", "manual_id": EP_SPLIT, "category": "tv", "object": "chunk 2/2",
         "uploaded_name": exported[1]["uploaded_name"], "status": "ambiguous",
         "expected_taken_local": "2022-02-05 19:49:46 GMT+08:00", "sha256": chunk["hash"]}]


@pytest.mark.parametrize("row, message", [
    (_edit("", account="films"), "account"),
    (_edit("", manual_id=""), "manual_id"),
    (_edit("", sha256="edited by hand"), "sha256"),
])
def test_a_row_naming_no_object_is_an_error_never_a_row(tmp_path, row, message):
    rows, errors = mvcommon.read_failed_csv_rows(_edited_csv(tmp_path, [row, _edit("")]))
    assert [r["manual_id"] for r in rows] == [EP_WHOLE]
    (error,) = errors
    assert message in error["error"] and error["line"] == 2


@pytest.mark.parametrize("delimiter,encoding,newline,sep_line", [
    (";", "utf-8-sig", "\r\n", False), ("\t", "utf-8", "\n", False), (";", "utf-8", "\r\n", True),
    (",", "cp1252", "\r\n", False)])
def test_the_rows_are_read_as_excel_tolerantly_as_the_edits(tmp_path, delimiter, encoding, newline, sep_line):
    rows = [_edit("", uploaded_name="Caf\N{LATIN SMALL LETTER E WITH ACUTE}.S01E01 [a1b2c3].mkv")]
    (row,), errors = mvcommon.read_failed_csv_rows(_edited_csv(tmp_path, rows, delimiter=delimiter, encoding=encoding,
                                                               newline=newline, sep_line=sep_line))
    assert errors == [] and row["uploaded_name"] == "Caf\N{LATIN SMALL LETTER E WITH ACUTE}.S01E01 [a1b2c3].mkv"


def test_a_foreign_csv_is_refused_like_read_failed_csv(tmp_path):
    path = tmp_path / "other.csv"
    path.write_text("name,size\nx,1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        mvcommon.read_failed_csv_rows(str(path))


def test_read_failed_csv_still_returns_only_the_edits(tmp_path, lib):
    mvcommon.append_failed(_failed())
    exported = _csv_rows(mvcommon.export_failed_csv(lib)["path"])
    edits, errors = mvcommon.read_failed_csv(_edited_csv(tmp_path, [exported[0], dict(exported[0], decision="skip")]))
    assert errors == [] and [(e["line"], e["decision"]) for e in edits] == [(3, "skip")]


# ===========================================================================
# 14.e — failed_objects: the failed list against the library (export_failed_csv's rows)
# ===========================================================================
def test_failed_objects_gives_the_newest_record_per_unconfirmed_object(lib):
    confirmed_lib = copy.deepcopy(lib)
    confirmed_lib[ANIME]["gphotos"] = mvcommon.new_gphotos_block(confirmed_lib[ANIME]["hash"], accounts={
        "anime": {"photo_id": PID[9], "upload_ms": 1, "verified_by": "dedupkey", "confirmed": True}})
    bts = lib[MOVIE]["extras"]["groups"]["Specials"]["items"][0]
    mvcommon.append_failed(_failed(at=T(1), reason="ambiguous"))
    newest = mvcommon.append_failed(_failed(at=T(2), reason="not-found"))
    extra = mvcommon.append_failed(_failed(account="movies", manual_id=MOVIE, sha256=bts["hash"], at=T(3),
                                           label="extra Specials/BTS.mkv", reason="s4-skipped"))
    mvcommon.append_failed(_failed(account="anime", manual_id=ANIME, sha256=lib[ANIME]["hash"], at=T(3)))
    mvcommon.append_failed(_failed(manual_id="tv-en-1999-gone-s01e01", sha256=_h("gone"), at=T(3)))

    listed = mvcommon.failed_objects(confirmed_lib)

    assert (listed["confirmed"], listed["orphans"]) == (1, 1)
    assert [(rec, obj["manual_id"], obj["kind"], obj["label"]) for rec, obj in listed["rows"]] == [
        (extra, MOVIE, "extra_whole", "extra Specials/BTS.mkv"), (newest, EP_WHOLE, "whole", "whole")]
    assert mvcommon.failed_objects(confirmed_lib, records=[newest])["rows"] == [listed["rows"][1]]



# ===========================================================================
# 14.f — resolve_photos: the harness
# ===========================================================================
R1, R2 = "tv-en-2015-demo-s04e01", "tv-en-2015-demo-s04e02"
N1, N2 = "Demo.S04E01.mkv", "Demo.S04E02.mkv"
S1, S2 = "r1r1r1", "r2r2r2"


@pytest.fixture()
def rp(fake_photos, monkeypatch, sandbox):
    """resolve_photos on the fake world: a terminal whose keyboard is `rp.keyboard(answer, ...)`, init_driver -> one fake
    driver per account session (no Chrome), the fetch lock a stand-in with a counting heartbeat, the debug port free,
    and cmd_dispatch_fetch a recorder (the re-fetch offer never spawns a child)."""
    drivers, beat, fetched = {}, _Beat(), []

    def init(profile_key="movies"):
        drivers.setdefault(profile_key, []).append(fake_photos.driver(profile_key))
        return drivers[profile_key][-1]

    @contextlib.contextmanager
    def lock():
        yield beat

    monkeypatch.setattr(mainfetch, "init_driver", init)
    monkeypatch.setattr(mainfetch, "_check_lock", lock)
    monkeypatch.setattr(mainfetch, "_debug_port_free", lambda *a, **k: True)
    monkeypatch.setattr(main, "_rp_stdin_is_tty", lambda: True)
    monkeypatch.setattr(main, "cmd_dispatch_fetch", lambda manual_id, *a, **k: fetched.append((manual_id, k)))

    def keyboard(*answers):
        monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{a}\n" for a in answers)))

    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 04"
    folder.mkdir(parents=True, exist_ok=True)
    return types.SimpleNamespace(world=fake_photos, drivers=drivers, beat=beat, keyboard=keyboard, folder=folder,
                                 fetched=fetched)


def _object(rp, mid, name, sid, data, capture=False, failed="not-found", account="tv", **row):
    """A whole-file episode `mid` holding `data` (with `capture`: its push capture — SHA-1 + DateUTC) in the library,
    and on the failed list (`failed` = its reason; None: not listed) -> its sha256."""
    if capture:
        _capture(rp.folder, sid, [_obj(data, _up(name, sid))])
    library = mvcommon.load_library()
    library[mid] = _whole(rp.folder, name, data, sid)
    mvcommon.save_library(library)
    sha256 = library[mid]["hash"]
    if failed:
        mvcommon.append_failed(dict({"account": account, "manual_id": mid, "label": "whole", "sha256": sha256,
                                     "uploaded_name": _up(name, sid), "reason": failed}, **row))
    return sha256


def _library_bytes(sandbox):
    return {k: (sandbox[k].read_bytes() if sandbox[k].exists() else None)
            for k in ("lib_movies", "lib_series", "lib_anime", "lib_others")}


def _url(item):
    return f"https://photos.google.com/photo/{item.photo_id}"


# ===========================================================================
# 14.f — resolve_photos: refusal, rows, the walk
# ===========================================================================
def test_a_pipe_is_refused_before_anything_runs(rp, capsys, monkeypatch):
    monkeypatch.setattr(main, "_rp_stdin_is_tty", REAL_STDIN_IS_TTY)              # the real check …
    monkeypatch.setattr(sys, "stdin", io.StringIO("p\n"))                          # … of a pipe

    def no_chrome(*args, **kwargs):
        raise AssertionError("Chrome must not be started")

    monkeypatch.setattr(mainfetch, "init_driver", no_chrome)
    _object(rp, R1, N1, S1, _bytes("r1"))

    res = main.cmd_resolve_photos()

    assert res["status"] == "refused" and mvcommon.read_resolved() == []
    assert "❌ resolve_photos is interactive: run it in a terminal" in capsys.readouterr().out


def test_a_matching_url_is_verified_by_its_content_key_and_the_apply_confirms_it(rp, sandbox, capsys):
    data = _bytes("r1-bytes")
    item = rp.world.add_item("tv", _up(N1, S1), data=data, taken=TAKEN)
    sha256 = _object(rp, R1, N1, S1, data, capture=True,
                     tried=[{"approach": "exact_name", "outcome": "not-found", "n": 0}])
    rp.keyboard("p", _url(item), "y")

    res = main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert res["status"] == "ok" and res["answered"] == 1 and res["walk"] == "done"
    assert f"   🧭 [1/1] {R1} · whole — not-found" in out
    assert f"      uploaded as   {_up(N1, S1)}" in out and "      tried         exact_name not-found(0)" in out
    assert "      ✔ verified — proof dedupkey; recorded: a fetch now goes straight to it" in out
    [learned] = mvcommon.read_learned("tv")
    assert (learned["photo_id"], learned["proof"], learned["source"]) == (item.photo_id, "dedupkey", "check")
    [answer] = mvcommon.read_resolved()
    assert (answer["answer"], answer["photo_id"], answer["proof"], answer["sha256"]) == (
        "url", item.photo_id, "dedupkey", sha256)
    assert "Apply these 1 changes to the library? [y/N]: " in out and f"    {R1} · whole — create  ← answered now" in out
    block = mvcommon.load_library()[R1]["gphotos"]["accounts"]["tv"]
    assert (block["photo_id"], block["verified_by"], block.get("confirmed")) == (item.photo_id, "dedupkey", True)
    assert rp.world.downloads == [] and list(rp.drivers) == ["tv"]                     # download-free, one session


def test_a_paste_of_the_item_the_browser_shows_needs_only_enter(rp, capsys, monkeypatch):
    data = _bytes("r1-bytes")
    item = rp.world.add_item("tv", _up(N1, S1), data=data, taken=TAKEN)
    _object(rp, R1, N1, S1, data)
    answers = iter(["p", "", "n"])

    def keyboard(prompt):           # the user opens the item in the session's tab, then just presses Enter
        if "photo URL" in prompt:
            rp.drivers["tv"][-1].get(_url(item))
        return next(answers)

    monkeypatch.setattr("builtins.input", keyboard)

    main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert "      ✔ verified — proof filename; recorded: a fetch now goes straight to it" in out
    assert [r["photo_id"] for r in mvcommon.read_learned("tv")] == [item.photo_id]


def test_another_item_asks_and_is_recorded_as_a_manual_match_the_apply_never_confirms(rp, capsys):
    data = _bytes("r1-legacy")
    renamed = rp.world.add_item("tv", "Renamed.By.Hand.mkv", data=data, taken="2021-01-01T00:00:00Z")
    _object(rp, R1, N1, S1, data)                                    # legacy: no content key, its name decides
    rp.keyboard("p", _url(renamed), "y", "y")

    main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert (f"      ? not verified — its name is 'Renamed.By.Hand.mkv', the object was uploaded as '{_up(N1, S1)}'"
            in out)
    assert "      accept as a manual match (a later fetch verifies it by SHA-256)? [y/N]: " in out
    assert f"      ✋ recorded as a manual match — `python main.py fetch {R1} --manual` opens it" in out
    [learned] = mvcommon.read_learned("tv")
    assert (learned["photo_id"], learned["proof"], learned["source"]) == (renamed.photo_id, "manual", "manual")
    block = mvcommon.load_library()[R1]["gphotos"]["accounts"]["tv"]
    assert block["photo_id"] == renamed.photo_id and "confirmed" not in block     # P-5: a later check confirms it


def test_a_manual_match_declined_records_nothing_and_asks_again(rp, capsys):
    data = _bytes("r1-legacy")
    renamed = rp.world.add_item("tv", "Renamed.By.Hand.mkv", data=data, taken="2021-01-01T00:00:00Z")
    _object(rp, R1, N1, S1, data)
    rp.keyboard("p", _url(renamed), "n", "k")

    main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert "      not recorded" in out and "      skipped — it stays on the failed list" in out
    assert mvcommon.read_learned("tv") == [] and mvcommon.read_resolved() == []


def test_proven_other_bytes_are_refused(rp, capsys):
    data = _bytes("r1-bytes")
    other = rp.world.add_item("tv", _up(N1, S1), data=_bytes("a transcode"), taken=TAKEN)
    _object(rp, R1, N1, S1, data, capture=True)
    rp.keyboard("p", _url(other), "k")

    main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert "      ✘ this item holds OTHER bytes than the object" in out
    assert "accept as a manual match" not in out
    assert mvcommon.read_learned("tv") == [] and mvcommon.read_resolved() == []


def test_an_item_of_another_account_is_named_as_such(rp, capsys):
    data = _bytes("r1-bytes")
    elsewhere = rp.world.add_item("movies", _up(N1, S1), data=data, taken=TAKEN)
    _object(rp, R1, N1, S1, data, capture=True)
    rp.keyboard("p", _url(elsewhere), "q")

    main.cmd_resolve_photos()

    assert "      ✘ the 'tv' account holds no such item — is it in another account?" in capsys.readouterr().out
    assert mvcommon.read_resolved() == []


@pytest.mark.parametrize("listed", [True, False], ids=["found", "not-found"])
def test_a_search_term_is_kept_and_tried_by_s3u_at_once(rp, capsys, listed):
    data = _bytes("r1-legacy")
    item = rp.world.add_item("tv", _up(N1, S1), data=data, taken=TAKEN)
    _object(rp, R1, N1, S1, data)
    rp.world.script_search("tv", "demo fourth season opener", [item.photo_id] if listed else [])
    rp.keyboard("s", "demo fourth season opener", "n")

    main.cmd_resolve_photos()

    out = capsys.readouterr().out
    learned = mvcommon.read_learned("tv")
    assert learned[0]["search_override"] == "demo fourth season opener" and learned[0]["source"] == "manual"
    [answer] = mvcommon.read_resolved()
    assert (answer["answer"], answer["search_term"], answer["found"]) == ("search", "demo fourth season opener", listed)
    if listed:
        assert "      ✔ your term found it — proof filename; recorded (and kept as its search term)" in out
        assert (learned[1]["photo_id"], learned[1]["source"], learned[1]["approach"]["tried"]) == (
            item.photo_id, "check", "s3u_user_query")
    else:   # [IMP-C25 thfix] no item to test the search with in this world: the zero answer is unverified
        assert ("      🔍 your term found no verified item (user_query not-found(0); unverified: no item known to be "
                "in 'tv' could show that its search was answering)") in out and len(learned) == 1
    queries = [e["request"][0] for e in rp.world.rpc_log if e["rpcid"] == "EzkLib"]
    assert queries == ["demo fourth season opener"]                                    # S3u alone, nothing else


def test_a_search_terms_zero_answer_is_verified_by_a_confirmed_record_in_the_library(rp, capsys):
    # IMP-C25 thfix: resolve_photos hands the guard its library — another object's CONFIRMED record in 'tv' is the
    # canary there (no crawl inventory), so the term's zero answer stands: no "unverified" marker.
    data = _bytes("r1-legacy")
    rp.world.add_item("tv", _up(N1, S1), data=data, taken=TAKEN)
    _object(rp, R1, N1, S1, data)
    _confirmed_elsewhere(rp.world, rp.folder)
    rp.world.script_search("tv", "demo fourth season opener", [])
    rp.keyboard("s", "demo fourth season opener", "n")

    main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert ("      🔍 your term found no verified item (user_query not-found(0)) — kept as its search term: every fetch "
            "tries it (S3u)") in out


def test_a_search_term_under_a_throttle_is_kept_but_no_answer_is_recorded(rp, capsys):
    # IMP-C25 thfix: S3u's zero answer meets a canary that finds nothing either -> that account's objects stop; the
    # user's term is kept (the object's search_override) but no answer is logged: the object is asked again next time.
    _canary_inventories(rp.world, ("tv",))
    _object(rp, R1, N1, S1, _bytes("r1-legacy"))
    rp.world.throttle("tv", after=0)
    rp.keyboard("s", "demo fourth season opener")

    res = main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert ("⏸️ Google Photos is throttled in 'tv' (a search for an item known to be there found nothing) — its "
            "objects stop here (no verdict for the one at hand: it comes back next time; the answers given so far "
            "are kept). Re-run resolve_photos in about 30 minutes.") in out
    assert "your term found no verified item" not in out and "logged out" not in out and res["status"] == "ok"
    assert mvcommon.read_resolved() == []                                              # asked again next time
    assert [r["search_override"] for r in mvcommon.read_learned("tv")] == ["demo fourth season opener"]


def test_not_uploaded_goes_to_the_imp_c27_list(rp, capsys):
    _object(rp, R1, N1, S1, _bytes("r1"))
    rp.keyboard("n")

    main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert "      recorded: not uploaded — listed in the IMP-C27 report at the end" in out
    assert "⚠️⚠️⚠️  YOU SAY NOT UPLOADED (IMP-C27): 1 object(s)" in out and f"    {R1} · whole — in 'tv'" in out
    assert [a["answer"] for a in mvcommon.read_resolved()] == ["not_uploaded"] and mvcommon.read_learned() == []


def test_quit_midway_keeps_the_answers_and_a_resumed_session_skips_them(rp, capsys):
    _object(rp, R1, N1, S1, _bytes("r1"))
    _object(rp, R2, N2, S2, _bytes("r2"))
    rp.keyboard("n", "q")

    first = main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert first["walk"] == "quit" and first["answered"] == 1 and f"🧭 [2/2] {R2}" in out
    rp.keyboard("k")

    main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert "— 1 object(s) to resolve ===" in out and "(1 answered before — left out)" in out
    assert f"🧭 [1/1] {R2}" in out and f"🧭 [1/1] {R1}" not in out


def test_a_newer_failure_brings_an_answered_object_back(rp, capsys):
    sha256 = _object(rp, R1, N1, S1, _bytes("r1"))
    mvcommon.append_resolved({"account": "tv", "manual_id": R1, "label": "whole", "sha256": sha256,
                              "answer": "not_uploaded", "at": "2026-01-01T00:00:00Z"})
    mvcommon.append_failed({"account": "tv", "manual_id": R1, "label": "whole", "sha256": sha256,
                            "reason": "not-found", "at": "2026-06-01T00:00:00Z"})      # failed again since
    rp.keyboard("k")

    main.cmd_resolve_photos()

    assert f"🧭 [1/1] {R1}" in capsys.readouterr().out


def test_the_end_of_input_quits_and_declines(rp, sandbox, capsys):
    data = _bytes("r1-bytes")
    item = rp.world.add_item("tv", _up(N1, S1), data=data, taken=TAKEN)
    _object(rp, R1, N1, S1, data, capture=True)
    rp.keyboard("p", _url(item))                         # then the input ends: no answer to the apply question
    before = _library_bytes(sandbox)

    res = main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert res["status"] == "ok" and res["applied"] is None
    assert "Not applied — the library is untouched." in out and _library_bytes(sandbox) == before
    assert [r["photo_id"] for r in mvcommon.read_learned("tv")] == [item.photo_id]   # … but the answer is kept


def test_a_declined_apply_leaves_the_library_untouched_and_is_offered_again(rp, sandbox, capsys):
    data = _bytes("r1-bytes")
    item = rp.world.add_item("tv", _up(N1, S1), data=data, taken=TAKEN)
    _object(rp, R1, N1, S1, data, capture=True)
    rp.keyboard("p", _url(item), "n")
    before = _library_bytes(sandbox)

    main.cmd_resolve_photos()

    assert _library_bytes(sandbox) == before
    assert "the next fetch's merge, check_photos --apply or resolve_photos folds them" in capsys.readouterr().out
    rp.keyboard("y")                                      # a later session: nothing left to walk, the apply offered

    later = main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert "Nothing to resolve." in out and later["applied"]["status"] == "saved"
    assert mvcommon.load_library()[R1]["gphotos"]["accounts"]["tv"]["photo_id"] == item.photo_id


def test_every_answer_is_written_before_the_next_question_and_ctrl_c_applies_nothing(rp, sandbox, capsys,
                                                                                       monkeypatch):
    _object(rp, R1, N1, S1, _bytes("r1"))
    _object(rp, R2, N2, S2, _bytes("r2"))
    answers = iter(["n"])

    def keyboard(prompt):
        try:
            return next(answers)
        except StopIteration:
            raise KeyboardInterrupt from None

    monkeypatch.setattr("builtins.input", keyboard)
    before = _library_bytes(sandbox)

    res = main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert res["status"] == "interrupted" and "🛑 Stopped by user — the answers given so far are kept" in out
    assert [a["manual_id"] for a in mvcommon.read_resolved()] == [R1]               # the first answer survived
    assert _library_bytes(sandbox) == before and "Apply these" not in out


def test_a_browser_fault_stops_the_walk_keeps_the_answers_and_applies_nothing(rp, sandbox, capsys, monkeypatch):
    from selenium.common.exceptions import WebDriverException
    data = _bytes("r2-bytes")
    item = rp.world.add_item("tv", _up(N2, S2), data=data, taken=TAKEN)
    _object(rp, R1, N1, S1, _bytes("r1"))
    _object(rp, R2, N2, S2, data, capture=True)

    def crashed(*args, **kwargs):
        raise WebDriverException("fake: chrome not reachable")

    monkeypatch.setattr(mainfetch, "verify_item", crashed)
    rp.keyboard("n", "p", _url(item), "y")
    before = _library_bytes(sandbox)

    res = main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert res["status"] == "error" and res["walk"] == "error"
    assert "❌ resolve_photos stopped: WebDriverException: Message: fake: chrome not reachable" in out
    assert [a["manual_id"] for a in mvcommon.read_resolved()] == [R1]               # the first answer is kept
    assert "Apply these" not in out and _library_bytes(sandbox) == before


def test_one_chrome_session_per_account_in_account_order(rp, capsys):
    movies_id = "mov-en-2019-film"
    folder = rp.folder.parent.parent.parent / "Movies" / "Film (2019)"
    folder.mkdir(parents=True)
    library = mvcommon.load_library()
    library[movies_id] = _whole(folder, "Film.2019.mkv", _bytes("film"), "f1f1f1")
    mvcommon.save_library(library)
    mvcommon.append_failed({"account": "movies", "manual_id": movies_id, "label": "whole",
                            "sha256": library[movies_id]["hash"], "reason": "not-found"})
    _object(rp, R1, N1, S1, _bytes("r1"))
    rp.keyboard("k", "k")

    main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert list(rp.drivers) == ["movies", "tv"] and out.index("=== 'movies'") < out.index("=== 'tv'")
    assert out.count("   > 🧹 Closed") == 2                                             # each session closed


def test_account_and_id_select_the_rows(rp, capsys):
    _object(rp, R1, N1, S1, _bytes("r1"))
    _object(rp, R2, N2, S2, _bytes("r2"), account="anime")
    rp.keyboard("k")

    main.cmd_resolve_photos(account="anime")

    out = capsys.readouterr().out
    assert "— 1 object(s) to resolve ===" in out and f"🧭 [1/1] {R2}" in out and list(rp.drivers) == ["anime"]
    rp.keyboard("k", "k")

    main.cmd_resolve_photos(manual_id="tv-en-2015-demo-s04")                         # a season prefix: both

    assert "— 2 object(s) to resolve ===" in capsys.readouterr().out


def test_download_lost_objects_are_listed_not_walked_and_offered_a_refetch(rp, capsys):
    _object(rp, R1, N1, S1, _bytes("r1"), failed="download-lost")
    rp.keyboard("y")

    res = main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert "Lost downloads: 1 object(s) — their item WAS found, the transfer broke twice" in out
    assert f"    python main.py fetch {R1}   (whole)" in out
    assert rp.drivers == {} and "🧭" not in out                                         # never identified again
    assert "Re-fetch the 1 lost download(s) now? (a fetch also folds the learned sidecar" in out
    assert rp.fetched == [(R1, {"fetch_extras": False})] and res["status"] == "ok"


def test_a_held_lock_refuses_without_chrome(rp, capsys, monkeypatch):
    _object(rp, R1, N1, S1, _bytes("r1"))

    @contextlib.contextmanager
    def held():
        raise mvcommon.LockHeldError("held")
        yield  # pragma: no cover

    monkeypatch.setattr(mainfetch, "_check_lock", held)
    rp.keyboard("k")

    res = main.cmd_resolve_photos()

    assert res["status"] == "refused" and rp.drivers == {}
    assert "holds the fetch session lock" in capsys.readouterr().out


def test_a_signed_out_profile_is_named_and_nothing_is_recorded(rp, capsys):
    _object(rp, R1, N1, S1, _bytes("r1"))
    rp.world.session["tv"] = "expired"
    rp.keyboard("k")

    res = main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert "❌ Profile 'tv' is logged out." in out and res["status"] == "ok"
    assert mvcommon.read_resolved() == []


def test_a_throttle_is_named_never_logged_out_and_nothing_is_recorded(rp, capsys):
    # IMP-C25 thfix: Google's "unusual traffic" page is no sign-out — the account's objects stop, the object at hand
    # gets no answer (it is asked again next time), nothing is recorded for it.
    _object(rp, R1, N1, S1, _bytes("r1"))
    rp.world.session["tv"] = "sorry"
    rp.keyboard("k")

    res = main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert ("⏸️ Google Photos is throttled in 'tv' (Google showed its 'unusual traffic' page) — its objects stop here "
            "(no verdict for the one at hand: it comes back next time; the answers given so far are kept). Re-run "
            "resolve_photos in about 30 minutes.") in out
    assert "logged out" not in out and res["status"] == "ok"
    assert mvcommon.read_resolved() == [] and mvcommon.read_learned() == []


def test_confirmed_and_vanished_objects_are_left_out(rp, capsys):
    data = _bytes("r1")
    sha256 = _object(rp, R1, N1, S1, data)
    library = mvcommon.load_library()
    library[R1]["gphotos"] = mvcommon.new_gphotos_block(sha256, accounts={"tv": {
        "photo_id": "AF1QipFAKEconfirmed000001", "upload_ms": 1, "verified_by": "dedupkey", "confirmed": True}})
    mvcommon.save_library(library)
    mvcommon.append_failed({"account": "tv", "manual_id": "tv-en-1999-gone-s01e01", "label": "whole",
                            "sha256": _h("gone"), "reason": "not-found"})

    res = main.cmd_resolve_photos()

    out = capsys.readouterr().out
    assert "— 0 object(s) to resolve ===" in out and "Nothing to resolve." in out and rp.drivers == {}
    assert res["walk"] is None


def test_a_csv_source_walks_its_rows(rp, capsys, tmp_path):
    _object(rp, R1, N1, S1, _bytes("r1"))
    _object(rp, R2, N2, S2, _bytes("r2"))
    mvcommon.export_failed_csv(mvcommon.load_library())
    rows = [r for r in _csv_rows(mvcommon.failed_csv_path()) if r["manual_id"] == R2]
    path = _edited_csv(tmp_path, rows)
    rp.keyboard("n")

    main.cmd_resolve_photos(source=path)

    out = capsys.readouterr().out
    assert f"=== RESOLVE PHOTOS — {path} — 1 object(s) to resolve ===" in out and f"🧭 [1/1] {R2}" in out
    assert [a["manual_id"] for a in mvcommon.read_resolved()] == [R2]


def test_an_unreadable_csv_is_an_error(rp, tmp_path):
    path = tmp_path / "other.csv"
    path.write_text("name,size\nx,1\n", encoding="utf-8")
    assert main.cmd_resolve_photos(source=str(path))["status"] == "error"
    assert main.cmd_resolve_photos(source=str(tmp_path / "missing.csv"))["status"] == "error"


# ===========================================================================
# 14.f — the command line; the check report (follow-up b)
# ===========================================================================
_RP_DEFAULTS = {"source": "failed", "account": None, "manual_id": None}


@pytest.mark.parametrize("tokens, expected", [
    ([], {}),
    (["failed"], {}),
    ([r"C:\Users\me\failed_objects.csv", "--account", "TV"], {"source": r"C:\Users\me\failed_objects.csv",
                                                               "account": "tv"}),
    (["--id", "tv-en-2015-demo-s04", "--account", "anime"], {"manual_id": "tv-en-2015-demo-s04", "account": "anime"}),
])
def test_the_command_line(tokens, expected):
    assert main.parse_resolve_photos_args(tokens) == dict(_RP_DEFAULTS, **expected)


@pytest.mark.parametrize("tokens, problem", [
    (["--account"], "--account needs a value"),
    (["--account", "music"], "--account must be one of movies, tv, anime, others"),
    (["--id"], "--id needs a value"),
    (["--id", "--account"], "--id needs a value"),
    (["a.csv", "b.csv"], "one source only"),
    (["--apply"], "unknown option --apply"),
])
def test_a_bad_command_line_names_its_problem(tokens, problem):
    with pytest.raises(ValueError) as err:
        main.parse_resolve_photos_args(tokens)
    assert problem in str(err.value)


def _dispatch(argv, status="ok"):
    import sys as real_sys

    class Sys:
        def __init__(self):
            self.argv = ["main.py", *argv]

        def __getattr__(self, name):
            return getattr(real_sys, name)

    calls = []
    namespace = dict(main.__dict__, sys=Sys(),
                     cmd_resolve_photos=lambda **kwargs: calls.append(kwargs) or {"status": status})
    exec(_main_guard(), namespace)
    return calls


def test_the_dispatcher_hands_the_options_over_and_exits_1_on_a_failed_run(capsys):
    assert _dispatch(["resolve_photos", "--account", "tv"]) == [dict(_RP_DEFAULTS, account="tv")]
    for status in main.RESOLVE_FAILED_STATUSES:
        with pytest.raises(SystemExit) as stop:
            _dispatch(["resolve_photos"], status=status)
        assert stop.value.code == 1
    with pytest.raises(SystemExit) as stop:
        _dispatch(["resolve_photos", "--frobnicate"])
    assert stop.value.code == 1
    out = capsys.readouterr().out
    assert "❌ resolve_photos: unknown option --frobnicate" in out and f"   Usage: {main.RESOLVE_USAGE}" in out
    with pytest.raises(SystemExit):
        _dispatch([])
    assert f"  {main.RESOLVE_USAGE}  — resolve the failed list by hand" in capsys.readouterr().out


def test_the_check_report_points_ambiguous_objects_at_resolve_photos(cw, sandbox, capsys):
    # Follow-up b: two same-name uploads holding different bytes — a legacy object cannot be told between them.
    cw.world.add_item("tv", _up("One.mkv", "on1on1"), data=_bytes("first"), taken="2022-01-01T00:00:00Z")
    cw.world.add_item("tv", _up("One.mkv", "on1on1"), data=_bytes("second"), taken="2023-01-01T00:00:00Z")
    _single(sandbox)

    res = main.cmd_check_photos(ONE)

    out = capsys.readouterr().out
    assert [r["status"] for r in res["rows"]] == ["ambiguous"]
    assert ("Ambiguous: 1 object(s) — candidates that cannot be told apart (an apply puts them on the failed list: "
            "pick the right item with python main.py resolve_photos failed)") in out
    assert "map_photos --resolutions" not in out
