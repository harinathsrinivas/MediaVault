"""IMP-C25 Step 14 — `resolve_photos`: the user resolves the failed list by hand (docs/feature-fetch-datetime/PLAN.md
Step 14 + "Follow-ups carried into Step 14" b; OD-14 ruling; P-5, P-10, P-18; the design decisions of
.candidates/c25-step14/STATUS.md).

  14.e  mvcommon's state for it: the answer log resolved.jsonl (append_resolved / read_resolved — resume, and the
        IMP-C27 "not uploaded" list), read_failed_csv_rows (every object row of a failed_objects.csv /
        resolutions_todo.csv, edited or not — read_failed_csv's Excel-tolerant reading, read_failed_csv unchanged) and
        failed_objects (the failed list against the library — export_failed_csv's rows, now shared)

Hermetic: every state dir is redirected (tests/conftest.py); synthetic ids only (every photo id contains "FAKE").
"""
import copy
import os
from datetime import datetime, timezone

import pytest

import mvcommon
from test_gphotos_primitives import (ANIME, EP_SPLIT, EP_WHOLE, MOVIE, PID, T, _csv_rows, _edit, _edited_csv,  # noqa: F401
                                     _failed, _h, lib)


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
