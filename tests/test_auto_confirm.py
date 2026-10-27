"""IMP-C25 Step 13b — auto-confirm unverified objects at the end of the next push (the OD-6 ruling; docs/feature-fetch-
datetime/PLAN.md P-11, P-19, Step 13b).

  13b.a  maybe_auto_confirm: the config switch, nothing pending, the web marker, only young pushes (the age boundary,
         an unknown push time), the Chrome profiles of the accounts the old objects route to, the fetch lock (held ·
         stale), the exact child command line through main._cp_spawn (the check's streaming idiom), the child failing /
         interrupted / not startable and any other error -> ONE warning line; it never raises
  13b.b  after_push_command — the success decision: False -> nothing (the library is not even read); a truthy result ->
         the auto-confirm; None -> the run's end state (prep_push_rep: its entry archived · prep_push_rep_season: every
         target of the run archived · push_group: every target uploaded — ranges, aliases, id prefixes); the switch
         read first; errors -> one warning line; tests/conftest.py's guard (every other test: inert recorders)
  13b.c  main.py's CLI dispatcher: each of the six push-family branches calls the hook once, AFTER its command returned,
         with what it returned (the push branch's extras-only path too); no other command reaches it; an exception or a
         usage error never does; success + an old pending object -> the exact child argv; every documented handled
         failure of the REAL commands -> nothing (output identical to a run without the hook); the child failing /
         interrupted / not startable never changes the push command's output or exit code
  13b.d  the rollback change-gate, instrumented: every RollbackJournal operation of a REAL prep_push_rep (prep, push,
         replace and its point of no return) and of a push rolled back before any upload is identical with and without
         the hook, the hook starts after the last of them, no journal file is on disk when it starts; the web console's
         push actions run in-process and never reach the hook (webui/server.py starts no Python child — unchanged)
  acfix  IMP-C25 follow-up (the user's finding of 2026-10-27, DECISIONS.md §1g: the check looked up the chunks pushed
         minutes earlier): the child is `check_photos pending --apply --min-age-h AUTO_CONFIRM_MIN_AGE_H --max-minutes
         15` (the argv pin, re-pinned on purpose); the line counts only the due objects and names the younger ones apart
         (", N newer skipped", only when there are any) — the reminder still counts every pending object; end to end, the
         check the auto-confirm starts hands only the old objects (and those with no push time) to the browser child

THE SIX RETURN CONTRACTS (PLAN Step 13b asks for them here — verified by reading main.py; the function names are
authoritative). "Succeeded" = returned normally — no exception: RollbackHardFail, SystemExit, KeyboardInterrupt, anything;
the dispatcher catches none, so none reaches the hook — and did not return False. A None is judged by the run's end
state (after_push_command / _push_command_completed), because three of the six return None on their failures too:

  push                        cmd_push -> True: a full push ("✅ SUCCESS.") or a `chunks N-M` partial push ("Partial
                              Upload Complete"). False on every handled failure: an unknown id, a bad temp dir, a missing
                              source, the adb mkdir failing, a resume with no recorded chunk / a damaged chunk, the
                              free-space and unsplittable refusals, a split or FLAC carry-out failure, an empty / invalid
                              chunk range, an upload failure (left resumable, or rolled back before any upload). Never
                              None. Its trailing --extras upload never changes the True. The branch's extras-only path (an
                              ARCHIVED main + --extras) returns _push_title_extras_or_warn -> push_title_extras: True iff
                              every extra uploaded (also when there is nothing to push), False when the title cannot be
                              resolved or an extra failed.
  push_group                  cmd_push_group -> None on EVERY path: success, an invalid range, no items, a bad temp dir,
                              too little space; a failed episode does not stop the loop and is not reported. -> succeeded
                              = every target of the run (season children or id prefix, the range, de-aliased) uploaded.
  prep_push_rep               cmd_prep_push_rep -> None on EVERY path: success ("✅✅✅ AUTO-PILOT COMPLETE"), the
                              unsplittable refusal, a prep failure (Aborted), a push failure (Paused), a replace failure,
                              and a post-PONR RollbackHardFail from replace (caught: "Auto-Pilot Stopped"). -> succeeded =
                              its entry (the id as given) archived.
  prep_push_rep_season        cmd_prep_push_rep_season -> None on EVERY path: success ("✅✅✅ SEASON AUTO-PILOT
                              COMPLETE"), the unsplittable refusal, a prep failure (no season), an invalid range, a bad
                              temp dir, too little space, a push failure mid-season and a RollbackHardFail (both caught,
                              the resume range printed). -> succeeded = every target of the run archived.
  prep_push_rep_enrich        cmd_prep_push_rep_enrich -> True once the archive completed (its entry archived — the
                              enrich leg only warns), False when it did not, or for -tvdbid (refused before anything runs).
  prep_push_rep_season_enrich cmd_prep_push_rep_season_enrich -> True once every target of the run is archived (the
                              enrich leg only warns), False otherwise, or for -tvdbid.

Hermetic: the sandbox library (tests/conftest.py), a fixed clock (mvcommon._now_utc), a runner standing in for
main._cp_spawn (tests/conftest.py's _hermetic_check_child refuses the real child — a child process would see none of a
test's patches: the REAL library, a REAL Chrome), the fetch lock moved under tmp_path by `real_auto_confirm`. No
network, no browser, no adb, no real C:\\Media, no real ~/.mediavault. Synthetic ids only.
"""
import ast
import functools
import hashlib
import json
import os
import shutil
import sys
import time
import types
from datetime import datetime, timedelta, timezone

import pytest

import gpcapture
import main
import mainfetch
import mvcommon

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
TV1, TV2, TV3 = "tv-en-2020-acshow-s01e01", "tv-en-2020-acshow-s01e02", "tv-en-2020-acshow-s01e03"
MOVIE = "mov-en-2021-acmovie"
SPORT = "oth-en-2022-acmatch"
CHILD_ARGV_TAIL = ["check_photos", "pending", "--apply", "--min-age-h", "24", "--max-minutes", "15"]   # acfix re-pin
SUMMARY = "🔎 check_photos pending: 2 object(s) — confirmed 2 · applied: 2 object(s) updated, 2 newly confirmed"


def _header(n, newer=0):
    """The auto-confirm's own line before the child runs: n = the DUE objects only; the younger ones are named apart,
    only when there are any (IMP-C25 acfix)."""
    skipped = f", {newer} newer skipped" if newer else ""
    return (f"🔎 {n} pushed object(s) await Google Photos confirmation — confirming them now{skipped} (check_photos "
            f"pending --apply --min-age-h 24, at most 15 min; to turn this off: \"auto_confirm_after_push\": false in "
            f"mvconfig.json)")


def _reminder(n):
    """PLAN Step 13b's reminder line, verbatim."""
    return f"🔎 {n} pushed object(s) await Google Photos confirmation — run: main.py check_photos pending --apply"


def _warning(why):
    return f"⚠️  Auto-confirm did not finish ({why}) — the push itself is done; confirm later with: main.py check_photos " \
           f"pending --apply"


def _iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _leaf(root, mid, hours_ago=30.0, confirmed=False, capture=True, push_time=True):
    """A whole-file library leaf (cmd_prep's keys) in its own folder whose capture records its push `hours_ago` before
    NOW — pending (P-11: unverified) unless `confirmed` (a trusted block holding a confirmed record). capture=False: a
    legacy object (no capture: the mapper's, never pending); push_time=False: the capture records the push without its
    time. -> (id, entry)."""
    folder = root / mid
    folder.mkdir(parents=True)
    data = f"AUTO-CONFIRM-BYTES:{mid}:".encode() * 20
    sha256 = hashlib.sha256(data).hexdigest()
    sid = mvcommon.generate_short_id(mid)
    if capture:
        push = {"device_id": "FAKEPIXEL", "objects": [{"role": "whole", "uploaded_name": f"{mid} [{sid}].mkv",
                                                        "sha256": sha256, "size_bytes": len(data)}]}
        if push_time:
            push["captured_at"] = _iso(NOW - timedelta(hours=hours_ago))
        doc = {"capture_version": 2, "short_id": sid, "prep": {}, "pushes": [push]}
        (folder / f"{sid}.gpcapture.json").write_text(json.dumps(doc), encoding="utf-8")
    entry = {"filename": f"{mid}.mkv", "short_id": sid, "folder_path": str(folder), "hash": sha256,
             "status": "archived", "uploaded": True}
    if confirmed:
        entry["gphotos"] = mvcommon.new_gphotos_block(sha256, accounts={"tv": {
            "photo_id": "AF1QipFAKEautoconfirm00001", "upload_ms": 1, "verified_by": "dedupkey", "source": "check",
            "confirmed": True}})
    return mid, entry


def _save(*leaves):
    library = dict(leaves)
    mvcommon.save_library(library)
    return library


class _Child:
    """main._cp_spawn's stand-in: records each argv and whether the fetch lock was free when the child would have
    started, prints the lines a real `check_photos pending --apply` child streams (by default only its summary), then
    answers (exit code, interrupted) — or raises."""

    def __init__(self, code=0, interrupted=False, lines=(SUMMARY,), raises=None):
        self.code, self.interrupted, self.lines, self.raises = code, interrupted, list(lines), raises
        self.calls, self.lock_free = [], []

    def __call__(self, argv):
        self.calls.append(list(argv))
        self.lock_free.append(not os.path.exists(mvcommon.FETCH_SESSION_LOCK))
        if self.raises is not None:
            raise self.raises
        for line in self.lines:
            print(line, flush=True)
        return self.code, self.interrupted


@pytest.fixture()
def ac(sandbox, real_auto_confirm, monkeypatch):
    """The REAL auto-confirm (its fetch lock under tmp_path), the clock fixed at NOW, no web marker, and a child runner
    that exits 0 after printing the check's summary. -> namespace(child, lock, root)."""
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: NOW)
    monkeypatch.delenv(main.AUTO_CONFIRM_WEB_JOB_ENV, raising=False)
    child = _Child()
    monkeypatch.setattr(main, "_cp_spawn", child)
    root = sandbox["local_root"] / "Items"
    root.mkdir(parents=True)
    return types.SimpleNamespace(child=child, lock=real_auto_confirm, root=root)


def _missing_profile(monkeypatch, tmp_path, *accounts):
    """Point these accounts' Chrome profiles at folders that do not exist (tests/conftest.py's
    _hermetic_chrome_profiles then runs the real pre-flight for them)."""
    profiles = dict(mainfetch.CHROME_PROFILES)
    for account in accounts:
        profiles[account] = str(tmp_path / "no_such_profile" / account)
    monkeypatch.setattr(mainfetch, "CHROME_PROFILES", profiles)


# ===========================================================================
# 13b.a — maybe_auto_confirm
# ===========================================================================
def test_an_old_pending_push_runs_the_exact_check_child_through_the_checks_spawner(ac, capsys):
    _save(_leaf(ac.root, TV1, hours_ago=30), _leaf(ac.root, TV2, hours_ago=2))

    assert main.maybe_auto_confirm() == "ran"

    abs_main = os.path.abspath(main.__file__)
    assert os.path.normcase(abs_main) == os.path.normcase(os.path.join(REPO, "main.py"))
    assert ac.child.calls == [[sys.executable, abs_main, "check_photos", "pending", "--apply", "--min-age-h", "24",
                               "--max-minutes", "15"]]   # PLAN Step 13b + IMP-C25 acfix (2026-10-27): exactly this argv
    assert main.auto_confirm_argv() == ac.child.calls[0] == [sys.executable, abs_main, *CHILD_ARGV_TAIL]
    # one line of its own — the due object counted, the young one (2 h) named apart — then the child's streamed output,
    # whose last line is the check's summary
    assert capsys.readouterr().out.splitlines() == [
        "🔎 1 pushed object(s) await Google Photos confirmation — confirming them now, 1 newer skipped (check_photos "
        "pending --apply --min-age-h 24, at most 15 min; to turn this off: \"auto_confirm_after_push\": false in "
        "mvconfig.json)", SUMMARY]


def test_the_child_finds_the_fetch_lock_free_and_none_is_left_behind(ac):
    _save(_leaf(ac.root, TV1, hours_ago=30))

    assert main.maybe_auto_confirm() == "ran"

    assert ac.child.lock_free == [True]                  # the probe released it: the check takes it itself
    assert not ac.lock.exists()


def test_a_library_handed_in_is_used_as_it_is(ac, monkeypatch, capsys):
    library = _save(_leaf(ac.root, TV1, hours_ago=30))
    monkeypatch.setattr(main, "load_library", lambda: pytest.fail("the caller's library must be used"))

    assert main.maybe_auto_confirm(library) == "ran"
    assert capsys.readouterr().out.splitlines() == [_header(1), SUMMARY]


def test_only_young_pushes_print_the_reminder_and_start_nothing(ac, capsys):
    _save(_leaf(ac.root, TV1, hours_ago=3), _leaf(ac.root, TV2, hours_ago=23.5))

    assert main.maybe_auto_confirm() == "young"

    assert ac.child.calls == []
    assert capsys.readouterr().out.splitlines() == [_reminder(2)]
    assert not ac.lock.parent.exists()                   # the lock was not even probed


@pytest.mark.parametrize("hours_ago,outcome", [(24.0, "ran"), (23.99, "young"), (24.01, "ran")])
def test_the_minimum_age_is_24_hours_inclusive(ac, hours_ago, outcome):
    assert main.AUTO_CONFIRM_MIN_AGE_H == 24             # the measured choice (main.py's comment; STATUS Log)
    _save(_leaf(ac.root, TV1, hours_ago=hours_ago))

    assert main.maybe_auto_confirm() == outcome
    assert len(ac.child.calls) == (outcome == "ran")


def test_an_unknown_push_time_counts_as_old(ac):
    # Step 13's verdict rule reads an unknown push time as old (possible-loss, not not-backed-up-yet) — the same here.
    _save(_leaf(ac.root, TV1, push_time=False))

    assert main.maybe_auto_confirm() == "ran"
    assert len(ac.child.calls) == 1


def test_one_old_object_is_enough_and_only_the_old_ones_are_counted(ac, capsys):
    # IMP-C25 acfix: one old object starts the check, which looks up only the old ones (--min-age-h) — the line counts
    # them and names the younger ones apart (until 2026-10-27 it counted, and the check looked up, all three)
    _save(_leaf(ac.root, TV1, hours_ago=50), _leaf(ac.root, TV2, hours_ago=1), _leaf(ac.root, TV3, hours_ago=0.1))

    assert main.maybe_auto_confirm() == "ran"
    assert capsys.readouterr().out.splitlines()[0] == _header(1, newer=2)
    assert ac.child.calls == [main.auto_confirm_argv()]


def test_with_no_younger_object_the_line_names_none(ac, capsys):
    # an unknown push time counts as old (due) here too; no ", 0 newer skipped"
    _save(_leaf(ac.root, TV1, hours_ago=30), _leaf(ac.root, TV2, push_time=False))

    assert main.maybe_auto_confirm() == "ran"
    assert capsys.readouterr().out.splitlines()[0] == (
        "🔎 2 pushed object(s) await Google Photos confirmation — confirming them now (check_photos pending --apply "
        "--min-age-h 24, at most 15 min; to turn this off: \"auto_confirm_after_push\": false in mvconfig.json)")


def test_nothing_pending_is_silent(ac, capsys):
    # confirmed (a trusted confirmed block) · legacy (no capture: the mapper's) — neither is pending (P-11)
    _save(_leaf(ac.root, TV1, hours_ago=90, confirmed=True), _leaf(ac.root, MOVIE, capture=False))

    assert main.maybe_auto_confirm() == "nothing"
    assert capsys.readouterr().out == "" and ac.child.calls == []


def test_an_empty_library_is_silent(ac, capsys):
    _save()
    assert main.maybe_auto_confirm() == "nothing"
    assert capsys.readouterr().out == ""


def test_turned_off_in_mvconfig_it_is_silent_and_reads_nothing(ac, monkeypatch, capsys):
    _save(_leaf(ac.root, TV1, hours_ago=30))
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"auto_confirm_after_push": False})
    monkeypatch.setattr(main, "load_library", lambda: pytest.fail("a disabled auto-confirm must not read the library"))

    assert main.maybe_auto_confirm() == "disabled"
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert ac.child.calls == [] and not ac.lock.parent.exists()


@pytest.mark.parametrize("config", [{}, {"auto_confirm_after_push": True}, {"auto_confirm_after_push": None},
                                    {"auto_confirm_after_push": True, "web": {"port": 8765}}])
def test_absent_null_or_true_leave_it_on(ac, monkeypatch, config):
    _save(_leaf(ac.root, TV1, hours_ago=30))
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", config)

    assert main.maybe_auto_confirm() == "ran"


@pytest.mark.parametrize("value", ["false", "true", 0, 1, "no", [], {}])
def test_a_non_boolean_switch_reads_as_off_with_one_warning(ac, monkeypatch, capsys, value):
    _save(_leaf(ac.root, TV1, hours_ago=30))
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"auto_confirm_after_push": value})

    assert main.maybe_auto_confirm() == "disabled"
    captured = capsys.readouterr()
    assert captured.out == "" and ac.child.calls == []
    assert captured.err.splitlines() == [f"⚠️  mvconfig.json: auto_confirm_after_push must be true or false, got "
                                         f"{value!r} — read as false (no auto-confirm after a push)."]


@pytest.mark.parametrize("marker", ["1", "yes", " 1 "])
def test_a_web_job_prints_the_reminder_only(ac, monkeypatch, capsys, marker):
    _save(_leaf(ac.root, TV1, hours_ago=30), _leaf(ac.root, TV2, hours_ago=1))
    monkeypatch.setenv("MEDIAVAULT_WEB_JOB", marker)

    assert main.maybe_auto_confirm() == "web"

    assert ac.child.calls == []                          # no Chrome from a web job
    assert capsys.readouterr().out.splitlines() == [_reminder(2)]
    assert not ac.lock.parent.exists()


@pytest.mark.parametrize("marker", ["", "0", " 0 "])
def test_an_unset_web_marker_is_no_web_job(ac, monkeypatch, marker):
    _save(_leaf(ac.root, TV1, hours_ago=30))
    monkeypatch.setenv("MEDIAVAULT_WEB_JOB", marker)

    assert main.maybe_auto_confirm() == "ran"


def test_a_web_job_with_nothing_pending_stays_silent(ac, monkeypatch, capsys):
    _save(_leaf(ac.root, TV1, hours_ago=30, confirmed=True))
    monkeypatch.setenv("MEDIAVAULT_WEB_JOB", "1")

    assert main.maybe_auto_confirm() == "nothing"
    assert capsys.readouterr().out == ""


def test_a_held_fetch_lock_prints_the_reminder_and_is_left_alone(ac, capsys):
    _save(_leaf(ac.root, TV1, hours_ago=30))
    ac.lock.parent.mkdir(parents=True)
    ac.lock.write_text("4242 1.0\n", encoding="utf-8")         # a running fetch / crawl / warm-up holds it

    assert main.maybe_auto_confirm() == "locked"

    assert ac.child.calls == []
    assert capsys.readouterr().out.splitlines() == [_reminder(1)]
    assert ac.lock.read_text(encoding="utf-8") == "4242 1.0\n"     # never taken over or removed


def test_a_stale_fetch_lock_does_not_hold_the_check_back(ac):
    # mvcommon.fetch_session_lock reclaims a lock older than an hour — the check would take it, so the probe does too
    _save(_leaf(ac.root, TV1, hours_ago=30))
    ac.lock.parent.mkdir(parents=True)
    ac.lock.write_text("4242 1.0\n", encoding="utf-8")
    old = time.time() - 2 * 3600
    os.utime(ac.lock, (old, old))

    assert main.maybe_auto_confirm() == "ran"
    assert ac.child.lock_free == [True] and not ac.lock.exists()


def test_no_profile_for_any_account_the_old_objects_route_to_prints_the_reminder(ac, monkeypatch, tmp_path, capsys):
    _save(_leaf(ac.root, TV1, hours_ago=30), _leaf(ac.root, SPORT, hours_ago=40))
    _missing_profile(monkeypatch, tmp_path, "tv", "others")

    assert main.maybe_auto_confirm() == "no-profile"

    assert ac.child.calls == []
    assert capsys.readouterr().out.splitlines() == [_reminder(2)]
    assert not ac.lock.parent.exists()


def test_one_missing_profile_of_two_still_runs_the_check(ac, monkeypatch, tmp_path):
    # The check child reports the objects of an account without a profile (no-profile errors, its remediation line)
    # and confirms the rest — so one existing profile is enough (assumption A1).
    _save(_leaf(ac.root, TV1, hours_ago=30), _leaf(ac.root, SPORT, hours_ago=40))
    _missing_profile(monkeypatch, tmp_path, "others")

    assert main.maybe_auto_confirm() == "ran"


def test_only_the_old_objects_accounts_count_for_the_profiles(ac, monkeypatch, tmp_path, capsys):
    # the old object routes to `others` (no profile); the young one's `tv` profile exists but nothing is due there
    _save(_leaf(ac.root, SPORT, hours_ago=40), _leaf(ac.root, TV1, hours_ago=1))
    _missing_profile(monkeypatch, tmp_path, "others")

    assert main.maybe_auto_confirm() == "no-profile"
    assert capsys.readouterr().out.splitlines() == [_reminder(2)]


def test_the_profile_check_follows_the_routing_override(ac, monkeypatch, tmp_path, capsys):
    # P-4: an mvconfig override routes the old tv episode to `others`, whose profile is missing
    _save(_leaf(ac.root, TV1, hours_ago=30))
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"fetch_account_overrides": {"tv-en-2020-acshow": "others"}})
    _missing_profile(monkeypatch, tmp_path, "others")

    assert main.maybe_auto_confirm() == "no-profile"
    assert capsys.readouterr().out.splitlines() == [_reminder(1)]


def test_a_failing_child_prints_one_warning_line(ac, monkeypatch, capsys):
    _save(_leaf(ac.root, TV1, hours_ago=30))
    child = _Child(code=1, lines=["❌ check_photos: the check could not run (exit code 1) — see the lines above."])
    monkeypatch.setattr(main, "_cp_spawn", child)

    assert main.maybe_auto_confirm() == "failed"
    assert capsys.readouterr().out.splitlines() == [
        _header(1), "❌ check_photos: the check could not run (exit code 1) — see the lines above.",
        _warning("check_photos exited with code 1")]


@pytest.mark.parametrize("code", [None, 1, 0])
def test_an_interrupted_child_prints_one_warning_line(ac, monkeypatch, capsys, code):
    _save(_leaf(ac.root, TV1, hours_ago=30))
    monkeypatch.setattr(main, "_cp_spawn", _Child(code=code, interrupted=True, lines=()))

    assert main.maybe_auto_confirm() == "failed"
    assert capsys.readouterr().out.splitlines() == [_header(1), _warning("interrupted")]


def test_a_partial_check_is_no_failure(ac, monkeypatch, capsys):
    # --max-minutes 15 reached: the check exits 0 (partial) and its summary says how to continue
    _save(_leaf(ac.root, TV1, hours_ago=30))
    partial = "🔎 check_photos pending: 1 object(s) — unchecked 1 — partial: re-run the same command to continue"
    monkeypatch.setattr(main, "_cp_spawn", _Child(lines=[partial]))

    assert main.maybe_auto_confirm() == "ran"
    assert capsys.readouterr().out.splitlines() == [_header(1), partial]


def test_a_child_that_cannot_start_prints_one_warning_line(ac, monkeypatch, capsys):
    _save(_leaf(ac.root, TV1, hours_ago=30))
    monkeypatch.setattr(main, "_cp_spawn", _Child(raises=FileNotFoundError(2, "The system cannot find the file")))

    assert main.maybe_auto_confirm() == "error"
    out = capsys.readouterr().out.splitlines()
    assert out == [_header(1), _warning("FileNotFoundError: [Errno 2] The system cannot find the file")]


@pytest.mark.parametrize("error,why", [
    (RuntimeError("boom\n  on a second line"), "RuntimeError: boom on a second line"),
    (SystemExit(1), "SystemExit: 1"),
    (KeyboardInterrupt(), "interrupted"),
])
def test_any_error_prints_one_warning_line_and_never_raises(ac, monkeypatch, capsys, error, why):
    _save(_leaf(ac.root, TV1, hours_ago=30))

    def broken(library, now):
        raise error
    monkeypatch.setattr(mvcommon, "unverified_objects", broken)

    assert main.maybe_auto_confirm() == "error"           # no exception escapes: the push's exit code stays
    assert capsys.readouterr().out.splitlines() == [_warning(why)]
    assert ac.child.calls == []


def test_a_corrupt_library_never_changes_the_exit_code(ac, sandbox, capsys):
    # load_library() sys.exit(1)s on a corrupt file (after its own CRITICAL lines) — contained: one warning line more
    sandbox["lib_series"].write_text("{not json", encoding="utf-8")

    assert main.maybe_auto_confirm() == "error"
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("❌ CRITICAL: Library file is corrupt")
    assert out[-1] == _warning("SystemExit: 1")
    assert sum(line.startswith("⚠️  Auto-confirm") for line in out) == 1


def test_the_auto_confirm_itself_writes_nothing(ac, sandbox, tmp_path):
    # Everything the confirmation records, the CHILD records (the apply engine, the sidecar, the failed list); this side
    # only reads — the library files and the Google Photos state are untouched, nothing is created but the lock folder.
    _save(_leaf(ac.root, TV1, hours_ago=30), _leaf(ac.root, TV2, hours_ago=2))
    libraries = {p: p.read_bytes() for p in sandbox["lib_movies"].parent.iterdir()}
    before = {p for p in tmp_path.rglob("*")}

    assert main.maybe_auto_confirm() == "ran"

    assert {p: p.read_bytes() for p in sandbox["lib_movies"].parent.iterdir()} == libraries
    assert {p for p in tmp_path.rglob("*")} - before == {ac.lock.parent, ac.lock.parent.parent}
    assert not os.path.exists(mvcommon.GPHOTOS_STATE_DIR)


# ===========================================================================
# 13b.b — after_push_command: the success decision
# ===========================================================================
SEASON = "tv-en-2020-acseason-s01"
FLAT = "tv-en-2020-acflat-s01"          # a "season" of plain leaves, no season_map: push_group's id-prefix mode
AUTO = "mov-en-2022-acauto"


def _state(root, mid, status, uploaded=None):
    """A leaf in a given state — no capture, so never pending itself (uploaded: onboarded / archived, unless given)."""
    mid, entry = _leaf(root, mid, capture=False)
    entry["status"] = status
    entry["uploaded"] = status in ("onboarded", "archived") if uploaded is None else uploaded
    return mid, entry


def _season(root, base, states):
    """A season_map `base` whose episodes e01… are in the given states -> [(id, entry)…] (the map first)."""
    children = [f"{base}e{n:02d}" for n in range(1, len(states) + 1)]
    leaves = [_state(root, cid, status) for cid, status in zip(children, states)]
    return [(base, {"type": "season_map", "folder_path": str(root), "total_episodes": len(children),
                    "children": children})] + leaves


def _old(root):
    """The old pending object that makes a success observable: with it, a succeeded command runs the check child."""
    return _leaf(root, MOVIE, hours_ago=30)


def test_a_command_that_returned_false_gets_nothing_and_nothing_is_read(ac, monkeypatch, capsys):
    _save(_old(ac.root))
    monkeypatch.setattr(main, "load_library", lambda: pytest.fail("a failed command must not even read the library"))

    assert main.after_push_command("push", False, MOVIE) is None
    assert capsys.readouterr().out == "" and ac.child.calls == []


@pytest.mark.parametrize("result", [True, 1, "ok"])
def test_a_command_that_returned_a_true_value_runs_the_auto_confirm(ac, capsys, result):
    _save(_old(ac.root))

    assert main.after_push_command("push", result, MOVIE) == "ran"
    assert capsys.readouterr().out.splitlines() == [_header(1), SUMMARY]


@pytest.mark.parametrize("command,result", [("push", True), ("prep_push_rep", None), ("push_group", None)])
def test_turned_off_the_hook_reads_nothing_whatever_the_result(ac, monkeypatch, capsys, command, result):
    _save(_old(ac.root), _state(ac.root, AUTO, "archived"))
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"auto_confirm_after_push": False})
    monkeypatch.setattr(main, "load_library", lambda: pytest.fail("a disabled auto-confirm must not read the library"))

    assert main.after_push_command(command, result, AUTO) == "disabled"
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "" and ac.child.calls == []


def test_an_invalid_switch_warns_once_per_push(ac, monkeypatch, capsys):
    _save(_old(ac.root))
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"auto_confirm_after_push": "off"})

    assert main.after_push_command("push", True, MOVIE) == "disabled"
    captured = capsys.readouterr()
    assert captured.out == "" and len(captured.err.splitlines()) == 1


@pytest.mark.parametrize("state,outcome", [
    ("archived", "ran"),        # ✅✅✅ AUTO-PILOT COMPLETE
    ("onboarded", None),        # pushed, then replace failed — or hard-failed past its PONR before its save
    ("local_ready", None),      # the push failed (Auto-Pilot Paused) or the unsplittable-track refusal
    (None, None),               # prep failed: no entry at all
])
def test_prep_push_rep_succeeded_when_its_entry_is_archived(ac, capsys, state, outcome):
    leaves = [_old(ac.root)] + ([_state(ac.root, AUTO, state)] if state else [])
    _save(*leaves)

    assert main.after_push_command("prep_push_rep", None, AUTO) == outcome
    assert len(ac.child.calls) == (outcome == "ran")
    if outcome is None:
        assert capsys.readouterr().out == ""


def test_prep_push_rep_on_an_alias_id_never_succeeds(ac):
    # cmd_prep refuses an alias id ("prep the primary instead") — the run archived nothing, whatever its primary's state
    primary, alias = f"{SEASON}e01", f"{SEASON}e02"
    _save(_old(ac.root), _state(ac.root, primary, "archived"),
          (alias, {"type": "multi_ep_alias", "alias_of": primary, "parent_id": SEASON}))

    assert main.after_push_command("prep_push_rep", None, alias) is None
    assert ac.child.calls == []


@pytest.mark.parametrize("states,episode_range,outcome", [
    (["archived", "archived", "archived"], None, "ran"),          # ✅✅✅ SEASON AUTO-PILOT COMPLETE
    (["archived", "onboarded", "archived"], None, None),          # an already-uploaded episode's replace failed
    (["archived", "local_ready", "local_ready"], None, None),     # stopped at e02 (push incomplete / a hard fail)
    (["local_ready", "archived", "archived"], "2-3", "ran"),      # only the run's own range counts
    (["archived", "archived", "local_ready"], "2-3", None),
    (["archived", "archived"], "x-y", None),                      # an invalid range: "❌ Invalid range."
    (["archived", "archived"], "5-6", None),                      # a range that selects nothing
])
def test_prep_push_rep_season_succeeded_when_every_target_of_the_run_is_archived(ac, states, episode_range, outcome):
    _save(_old(ac.root), *_season(ac.root, SEASON, states))

    assert main.after_push_command("prep_push_rep_season", None, SEASON, episode_range) == outcome
    assert len(ac.child.calls) == (outcome == "ran")


def test_prep_push_rep_season_whose_season_was_never_created_never_succeeds(ac):
    # "❌ Prep failed. Base ID not found." — nothing to judge, nothing archived
    _save(_old(ac.root))
    assert main.after_push_command("prep_push_rep_season", None, SEASON) is None


def test_a_season_alias_child_is_judged_as_its_primary(ac):
    # the combined episode e02 is an alias of e01 (archived): the run's targets de-alias to [e01]
    season = _season(ac.root, SEASON, ["archived"])
    season[0][1]["children"].append(f"{SEASON}e02")
    _save(_old(ac.root), *season, (f"{SEASON}e02", {"type": "multi_ep_alias", "alias_of": f"{SEASON}e01",
                                                    "parent_id": SEASON}))

    assert main.after_push_command("prep_push_rep_season", None, SEASON) == "ran"


@pytest.mark.parametrize("states,episode_range,outcome", [
    (["onboarded", "onboarded", "archived"], None, "ran"),        # uploaded: onboarded and archived alike
    (["onboarded", "local_ready", "onboarded"], None, None),      # one episode's push failed — the loop went on
    (["local_ready", "onboarded", "onboarded"], "2-3", "ran"),
    (["onboarded", "onboarded"], "bad", None),                    # "❌ Invalid episode range format"
])
def test_push_group_on_a_season_succeeded_when_every_target_is_uploaded(ac, states, episode_range, outcome):
    _save(_old(ac.root), *_season(ac.root, SEASON, states))

    assert main.after_push_command("push_group", None, SEASON, episode_range) == outcome


@pytest.mark.parametrize("episode_range,outcome", [("1-2", "ran"), (None, None), ("3-3", None)])
def test_push_group_by_id_prefix_succeeded_when_every_matching_target_is_uploaded(ac, episode_range, outcome):
    # no season_map: every other id starting with the group id (sorted), then the range — cmd_push_group's prefix mode
    _save(_old(ac.root), _state(ac.root, f"{FLAT}e01", "onboarded"), _state(ac.root, f"{FLAT}e02", "archived"),
          _state(ac.root, f"{FLAT}e03", "local_ready"), _state(ac.root, "tv-en-2020-acother-s01e01", "local_ready"))

    assert main.after_push_command("push_group", None, FLAT, episode_range) == outcome


def test_push_group_that_matched_nothing_never_succeeds(ac):
    # "❌ No items found to push."
    _save(_old(ac.root))
    assert main.after_push_command("push_group", None, "tv-en-2099-nothing-s01") is None


@pytest.mark.parametrize("command", ["push", "prep_push_rep_enrich", "prep_push_rep_season_enrich", "sort"])
def test_a_none_from_a_command_that_never_returns_none_is_no_success(ac, command):
    _save(_old(ac.root), _state(ac.root, AUTO, "archived"))
    assert main.after_push_command(command, None, AUTO) is None
    assert ac.child.calls == []


def test_an_unreadable_library_in_the_success_decision_prints_one_warning(ac, sandbox, capsys):
    sandbox["lib_movies"].write_text("{broken", encoding="utf-8")

    assert main.after_push_command("prep_push_rep", None, AUTO) == "error"     # never raises: the exit code stays
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == _warning("SystemExit: 1")
    assert sum(line.startswith("⚠️  Auto-confirm") for line in out) == 1


def test_ctrl_c_during_the_success_decision_prints_one_warning(ac, monkeypatch, capsys):
    def interrupted():
        raise KeyboardInterrupt
    monkeypatch.setattr(main, "load_library", interrupted)

    assert main.after_push_command("push", True, MOVIE) == "error"
    assert capsys.readouterr().out.splitlines() == [_warning("interrupted")]


def test_every_other_test_gets_an_inert_auto_confirm(sandbox, capsys):
    # tests/conftest.py's autouse _hermetic_auto_confirm, without the real_auto_confirm opt-in: both entry points record
    # their calls and do nothing — no library read, no line, no lock, no child — so a dispatcher test sees the push
    # command exactly as before.
    from conftest import _InertAutoConfirm
    root = sandbox["local_root"] / "Items"
    _save(_leaf(root, MOVIE, hours_ago=30))

    assert isinstance(main.after_push_command, _InertAutoConfirm)
    assert isinstance(main.maybe_auto_confirm, _InertAutoConfirm)
    assert main.after_push_command("push", True, MOVIE) is None
    assert main.after_push_command.calls == [(("push", True, MOVIE), {})]
    assert capsys.readouterr().out == ""


# ===========================================================================
# 13b.c — main.py's CLI dispatcher
# ===========================================================================
@functools.lru_cache(maxsize=1)
def _main_guard():
    """main.py's `if __name__ == "__main__":` body, compiled once — the harness of tests/test_prep_push_rep_enrich_cli.py
    and tests/test_prep_keeps_split_record.py (line numbers point at the real main.py)."""
    with open(main.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=main.__file__)
    guard = next(node for node in tree.body if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                 and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")
    return compile(ast.Module(body=guard.body, type_ignores=[]), main.__file__, "exec")


class _Argv:
    """`sys` for the dispatcher: the test's argv; everything else is the real module (pytest's argv is never touched)."""

    def __init__(self, argv):
        self.argv = ["main.py", *argv]

    def __getattr__(self, name):
        return getattr(sys, name)


def _dispatch(argv, **names):
    """`python main.py <argv>` without the process: main.py's dispatcher run in a copy of main's namespace taken NOW (so
    it sees the hook this test installed), with `names` swapped in (e.g. cmd_push=…). Returning normally is what makes a
    real run exit 0; otherwise it raises what the dispatcher raises (SystemExit for a usage error)."""
    exec(_main_guard(), dict(main.__dict__, sys=_Argv(argv), **names))


class _Command:
    """A push-family command stand-in: notes its call in `events`, prints its own line, runs `effect` (the end state a
    real run leaves in the library), then returns `result` — or raises."""

    def __init__(self, events, name, result=True, raises=None, effect=None):
        self.events, self.name, self.result, self.raises, self.effect = events, name, result, raises, effect

    def __call__(self, *args, **kwargs):
        self.events.append(("cmd", self.name))
        print(f"<{self.name} ran>")
        if self.effect is not None:
            self.effect()
        if self.raises is not None:
            raise self.raises
        return self.result


def _hook_recorder(monkeypatch, events):
    """main.after_push_command -> a recorder appending ("hook", its arguments) to `events`."""
    monkeypatch.setattr(main, "after_push_command", lambda *args: events.append(("hook", args)))


def _set(ids, status):
    """The end state a real run leaves: these entries now `status` (uploaded with onboarded / archived)."""
    library = mvcommon.load_library()
    for mid in ids:
        library[mid]["status"] = status
        library[mid]["uploaded"] = status in ("onboarded", "archived")
    mvcommon.save_library(library)


PUSH_FAMILY = ("cmd_push", "cmd_push_group", "cmd_prep_push_rep", "cmd_prep_push_rep_season", "cmd_prep_push_rep_enrich",
               "cmd_prep_push_rep_season_enrich")
# (argv, the function the branch calls, what it returns, the hook's arguments) — one line per push-family branch
WIRING = [
    (["push", MOVIE], "cmd_push", True, ("push", True, MOVIE)),
    (["push", MOVIE, "SIZE_GB", "4", "chunks", "1-2", "device", "series"], "cmd_push", False, ("push", False, MOVIE)),
    (["push_group", SEASON, "episodes", "1-3"], "cmd_push_group", None, ("push_group", None, SEASON, "1-3")),
    (["push_group", FLAT, "SIZE_MB", "700"], "cmd_push_group", None, ("push_group", None, FLAT, None)),
    (["prep_push_rep", AUTO, r"Z:\Movies\Auto (2022)\Auto.mkv"], "cmd_prep_push_rep", None,
     ("prep_push_rep", None, AUTO)),
    (["prep_push_rep_season", SEASON, r"Z:\Series\S01", "episodes", "2-4", "COUNT", "3"], "cmd_prep_push_rep_season",
     None, ("prep_push_rep_season", None, SEASON, "2-4")),
    (["prep_push_rep_enrich", AUTO, r"Z:\Movies\Auto.mkv", "-tmdbid", "550", "--yes"], "cmd_prep_push_rep_enrich",
     True, ("prep_push_rep_enrich", True, AUTO)),
    (["prep_push_rep_season_enrich", SEASON, r"Z:\Series\S01"], "cmd_prep_push_rep_season_enrich", False,
     ("prep_push_rep_season_enrich", False, SEASON, None)),
]
ONE_PER_BRANCH = [WIRING[i] for i in (0, 2, 4, 5, 6, 7)]


@pytest.mark.parametrize("argv,function,result,hook_args", WIRING, ids=[f"{w[0][0]}-{i}" for i, w in enumerate(WIRING)])
def test_each_push_family_branch_calls_the_hook_once_after_its_command(sandbox, monkeypatch, argv, function, result,
                                                                       hook_args):
    events = []
    _hook_recorder(monkeypatch, events)

    _dispatch(argv, **{function: _Command(events, function, result)})

    assert events == [("cmd", function), ("hook", hook_args)]      # once, AFTER the command, with what it returned


def test_the_push_branchs_extras_only_path_hands_its_result_to_the_hook(ac, monkeypatch, capsys):
    # an ARCHIVED main + --extras pushes the extras only (IMP-D19 Card D): that upload's result is the command's
    _save(_old(ac.root))                                              # MOVIE is archived
    events = []
    _hook_recorder(monkeypatch, events)

    _dispatch(["push", MOVIE, "--extras", "Specials"],
              _push_title_extras_or_warn=_Command(events, "_push_title_extras_or_warn", False),
              cmd_push=_Command(events, "cmd_push"))

    assert events == [("cmd", "_push_title_extras_or_warn"), ("hook", ("push", False, MOVIE))]


@pytest.mark.parametrize("argv,function", [
    (["prep", AUTO, r"Z:\Movies\Auto.mkv"], "cmd_prep"),
    (["replace", MOVIE], "cmd_replace"),
    (["replace_group", SEASON], "cmd_replace_group"),
    (["restore", MOVIE], "cmd_restore"),
    (["fetch", MOVIE], "cmd_dispatch_fetch"),
    (["fetch_restore", MOVIE], "cmd_fetch_restore"),
    (["add_extras", MOVIE, "Specials"], "cmd_add_extras"),
    (["sort"], "cmd_sort"),
])
def test_no_other_command_reaches_the_hook(sandbox, monkeypatch, argv, function):
    events = []
    _hook_recorder(monkeypatch, events)

    _dispatch(argv, **{function: _Command(events, function)})

    assert events == [("cmd", function)]


def test_the_auto_confirms_own_child_command_line_is_a_valid_check_and_never_reaches_the_hook(sandbox, monkeypatch):
    events, checks = [], []
    _hook_recorder(monkeypatch, events)

    _dispatch(main.auto_confirm_argv()[2:], cmd_check_photos=lambda **kw: checks.append(kw) or {"status": "ok"})

    assert [(c["scope"], c["apply"], c["min_age_h"], c["max_minutes"]) for c in checks] == [("pending", True, 24, 15)]
    assert events == []                                               # no hook in the child: no recursion


@pytest.mark.parametrize("argv,function,result,hook_args", ONE_PER_BRANCH, ids=[w[1] for w in ONE_PER_BRANCH])
@pytest.mark.parametrize("error", [RuntimeError("boom"), main.RollbackHardFail("X archived", "failed past the PONR",
                                                                               "fetch_restore X"),
                                   SystemExit(1), KeyboardInterrupt()],
                         ids=["RuntimeError", "RollbackHardFail", "SystemExit", "KeyboardInterrupt"])
def test_an_exception_never_reaches_the_hook(ac, capsys, argv, function, result, hook_args, error):
    # the REAL hook, and an old pending object: had the hook been reached, it would have started the check
    _save(_old(ac.root))

    with pytest.raises(type(error)):
        _dispatch(argv, **{function: _Command([], function, raises=error)})

    assert ac.child.calls == []
    assert capsys.readouterr().out.splitlines() == [f"<{function} ran>"]


@pytest.mark.parametrize("argv", [["push"], ["push", MOVIE, "SIZE_GB"], ["push", MOVIE, "--extras"], ["push_group"],
                                  ["push_group", SEASON, "episodes"], ["prep_push_rep", AUTO],
                                  ["prep_push_rep_season", SEASON], ["prep_push_rep_enrich", AUTO],
                                  ["prep_push_rep_season_enrich", SEASON]])
def test_a_usage_error_never_reaches_the_hook(ac, argv):
    _save(_old(ac.root))
    events = []
    stubs = {name: _Command(events, name) for name in PUSH_FAMILY + ("_push_title_extras_or_warn",)}

    with pytest.raises(SystemExit) as stop:
        _dispatch(argv, **stubs)

    assert stop.value.code == 1 and events == [] and ac.child.calls == []


@pytest.mark.parametrize("argv,function,result,end_state,lead", [
    (["push", AUTO], "cmd_push", True, None, []),
    (["push", MOVIE, "--extras", "Specials"], "_push_title_extras_or_warn", True, None,
     [f"ℹ️  {MOVIE} main is archived — pushing extras only (main untouched)."]),
    (["push_group", SEASON], "cmd_push_group", None, ("onboarded", [f"{SEASON}e01", f"{SEASON}e02"]), []),
    (["push_group", SEASON, "episodes", "2-2"], "cmd_push_group", None, ("onboarded", [f"{SEASON}e02"]), []),
    (["prep_push_rep", AUTO, r"Z:\Movies\Auto.mkv"], "cmd_prep_push_rep", None, ("archived", [AUTO]), []),
    (["prep_push_rep_season", SEASON, r"Z:\Series\S01"], "cmd_prep_push_rep_season", None,
     ("archived", [f"{SEASON}e01", f"{SEASON}e02"]), []),
    (["prep_push_rep_enrich", AUTO, r"Z:\Movies\Auto.mkv"], "cmd_prep_push_rep_enrich", True, None, []),
    (["prep_push_rep_season_enrich", SEASON, r"Z:\Series\S01"], "cmd_prep_push_rep_season_enrich", True, None, []),
], ids=["push", "push-extras-only", "push_group", "push_group-range", "prep_push_rep", "prep_push_rep_season",
        "prep_push_rep_enrich", "prep_push_rep_season_enrich"])
def test_a_succeeded_push_family_command_then_runs_the_exact_check_child(ac, capsys, argv, function, result, end_state,
                                                                         lead):
    _save(_old(ac.root), _state(ac.root, AUTO, "local_ready"), *_season(ac.root, SEASON, ["local_ready"] * 2))
    effect = (lambda: _set(end_state[1], end_state[0])) if end_state else None

    _dispatch(argv, **{function: _Command([], function, result, effect=effect)})      # returns: the run exits 0

    assert ac.child.calls == [[sys.executable, os.path.abspath(main.__file__), *CHILD_ARGV_TAIL]]
    assert capsys.readouterr().out.splitlines() == lead + [f"<{function} ran>", _header(1), SUMMARY]


@pytest.mark.parametrize("argv,function,returned", [
    (["push", "mov-en-2099-nosuchid"], "cmd_push", False),                               # ❌ ID not found.
    (["push_group", "tv-en-2099-nosuchgroup-s01"], "cmd_push_group", None),              # ❌ No items found to push.
    (["prep_push_rep", AUTO, "<missing>"], "cmd_prep_push_rep", None),                   # ❌ File not found -> Aborted
    (["prep_push_rep_season", SEASON, "<missing>"], "cmd_prep_push_rep_season", None),   # ❌ Folder not found -> Prep failed
    (["prep_push_rep_enrich", AUTO, "<missing>"], "cmd_prep_push_rep_enrich", False),    # the archive did not complete
    (["prep_push_rep_enrich", AUTO, "<missing>", "-tvdbid", "1"], "cmd_prep_push_rep_enrich", False),         # refused
    (["prep_push_rep_season_enrich", SEASON, "<missing>", "-tvdbid", "1"], "cmd_prep_push_rep_season_enrich", False),
], ids=["push", "push_group", "prep_push_rep", "prep_push_rep_season", "prep_push_rep_enrich",
        "prep_push_rep_enrich-tvdbid", "prep_push_rep_season_enrich-tvdbid"])
def test_a_failed_push_family_command_gets_nothing_from_the_hook(ac, monkeypatch, capsys, tmp_path, argv, function,
                                                                 returned):
    # The REAL commands on their documented handled failures (the return contracts above, pinned here) with an old
    # pending object in the library: a hook that fired would print its line and start the check. Its output must be
    # exactly the command's own — the same as a run without any hook.
    _save(_old(ac.root))
    argv = [str(tmp_path / "missing" / "Auto.mkv") if token == "<missing>" else token for token in argv]
    real, returns = getattr(main, function), []

    def spy(*args, **kwargs):
        returns.append(real(*args, **kwargs))
        return returns[-1]

    real_hook = main.after_push_command                                 # the real one (the `ac` fixture restored it)
    monkeypatch.setattr(main, "after_push_command", lambda *args: None)
    _dispatch(argv, **{function: spy})
    without_hook = capsys.readouterr().out
    monkeypatch.setattr(main, "after_push_command", real_hook)
    _dispatch(argv, **{function: spy})

    assert capsys.readouterr().out == without_hook and "❌" in without_hook
    assert returns == [returned, returned]                              # the documented contract
    assert ac.child.calls == []


@pytest.mark.parametrize("case", ["young", "locked", "disabled", "web"])
def test_after_a_push_the_no_chrome_cases_print_at_most_the_reminder(ac, monkeypatch, capsys, case):
    # PLAN Step 13b's test list at the CLI level: only young -> reminder · lock held -> reminder · disabled -> nothing ·
    # web marker -> reminder only
    _save(_leaf(ac.root, TV1, hours_ago=2 if case == "young" else 30))
    if case == "locked":
        ac.lock.parent.mkdir(parents=True)
        ac.lock.write_text("4242 1.0\n", encoding="utf-8")
    elif case == "disabled":
        monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"auto_confirm_after_push": False})
    elif case == "web":
        monkeypatch.setenv("MEDIAVAULT_WEB_JOB", "1")

    _dispatch(["push", AUTO], cmd_push=_Command([], "cmd_push", True))

    assert ac.child.calls == []
    assert capsys.readouterr().out.splitlines() == ["<cmd_push ran>"] + ([] if case == "disabled" else [_reminder(1)])


@pytest.mark.parametrize("child,why", [
    (lambda: _Child(code=1, lines=["❌ check_photos: the check could not run (exit code 1) — see the lines above."]),
     "check_photos exited with code 1"),
    (lambda: _Child(code=None, interrupted=True, lines=["🛑 No longer waiting — the check closes its Chrome on its own."]),
     "interrupted"),
    (lambda: _Child(raises=OSError("cannot start the child")), "OSError: cannot start the child"),
], ids=["exit-1", "interrupted", "not-startable"])
def test_the_child_failing_never_changes_the_push_commands_output_or_exit_code(ac, monkeypatch, capsys, child, why):
    _save(_old(ac.root))
    monkeypatch.setattr(main, "_cp_spawn", child())

    _dispatch(["push", AUTO], cmd_push=_Command([], "cmd_push", True))     # returns normally: the run still exits 0

    out = capsys.readouterr().out.splitlines()
    assert out[:2] == ["<cmd_push ran>", _header(1)]          # the push's own output, unchanged and first
    assert out[-1] == _warning(why)
    assert sum(line.startswith("⚠️  Auto-confirm") for line in out) == 1


def test_a_library_broken_after_the_push_never_changes_its_exit_code(ac, sandbox, capsys):
    # load_library() sys.exit(1)s on a corrupt file — inside the hook that SystemExit is contained (one warning line)
    def push(*args, **kwargs):
        print("<cmd_push ran>")
        sandbox["lib_series"].write_text("{not json", encoding="utf-8")
        return True

    _dispatch(["push", AUTO], cmd_push=push)                                # no SystemExit escapes

    out = capsys.readouterr().out.splitlines()
    assert out[0] == "<cmd_push ran>" and out[-1] == _warning("SystemExit: 1")
    assert ac.child.calls == []


# ===========================================================================
# 13b.d — the rollback change-gate, instrumented; the web console
# ===========================================================================
PINNED = "mov-en-2022-journalpin"


def _fake_split(monkeypatch, works=True):
    """mkvmerge --split -> a byte slicer honouring split_video_file's contract (2 chunks that concatenate back to the
    master) — or, works=False, a split that produces nothing (cmd_push then rolls its journal back before any upload);
    no unsplittable track; a huge disk."""
    def split(input_path, output_dir, method, value_str, file_id=""):
        if not works:
            return []
        with open(input_path, "rb") as fh:
            data = fh.read()
        half = len(data) // 2 + 1
        stem = os.path.splitext(os.path.basename(input_path))[0]
        paths = []
        for n, part in enumerate((data[:half], data[half:]), 1):
            path = os.path.join(output_dir, f"{stem} [{file_id}].chunk.{n:03d}.mkv")
            with open(path, "wb") as fh:
                fh.write(part)
            paths.append(path)
        return paths

    monkeypatch.setattr(main, "split_video_file", split)
    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda path: [])
    huge = 500 * 1024 ** 3
    monkeypatch.setattr(main.shutil, "disk_usage", lambda path: types.SimpleNamespace(total=huge, used=0, free=huge))


def _journal_log(monkeypatch, root, events):
    """Instrument every RollbackJournal operation — behaviour unchanged, each original still runs — appending to `events`:
    ("open", folder, id) · ("record", the record) · ("ponr", id) · ("commit", id) · ("rollback", id); paths relative to
    `root`, so two runs in the same folder compare equal."""
    journal = main.RollbackJournal
    real = {name: getattr(journal, name) for name in ("__init__", "_append", "mark_point_of_no_return", "commit",
                                                       "rollback")}

    def rel(value):
        return os.path.relpath(value, root) if isinstance(value, str) and os.path.isabs(value) else value

    def open_(self, folder_path, manual_id):
        events.append(("open", rel(folder_path), manual_id))
        real["__init__"](self, folder_path, manual_id)

    def append(self, record):
        events.append(("record", {key: rel(value) for key, value in record.items()}))
        real["_append"](self, record)

    def ponr(self):
        events.append(("ponr", self.manual_id))
        real["mark_point_of_no_return"](self)

    def commit(self):
        events.append(("commit", self.manual_id))
        real["commit"](self)

    def rollback(self, library):
        events.append(("rollback", self.manual_id))
        return real["rollback"](self, library)

    for name, fn in (("__init__", open_), ("_append", append), ("mark_point_of_no_return", ponr), ("commit", commit),
                     ("rollback", rollback)):
        monkeypatch.setattr(journal, name, fn)


def _fresh_title(sandbox, make_video, mock_device, old):
    """The same starting point for every run, in the same folder: a fresh title folder holding the master, an empty fake
    device, a library holding only the old pending object `old`. -> the master's path."""
    folder = sandbox["local_root"] / "Movies" / "Journal Pin (2022)"
    if folder.exists():
        shutil.rmtree(folder)
    for item in mock_device.iterdir():
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    folder.mkdir(parents=True)
    master, _sha256 = make_video(folder / "Journal.Pin.2022.mkv")
    mvcommon.save_library(dict([old]))
    return master


def _journals(root):
    """Every rollback journal on disk under `root` (the live name and the preserved `.mediavault_txn.<ts>.json`)."""
    return sorted(str(p) for p in root.rglob(".mediavault_txn*"))


def test_with_or_without_the_hook_a_real_archive_journals_exactly_the_same_and_the_hook_comes_last(
        ac, sandbox, mock_device, make_video, stub_tech_specs, fake_dummy, monkeypatch, capsys):
    # PLAN Step 13b (the rollback change-gate): a REAL `prep_push_rep <id> <master> COUNT 2` through the dispatcher —
    # cmd_prep's, cmd_push's and cmd_replace's journals, replace's point of no return — once without the hook, once with
    # it (an old pending object makes it start the check). The journal operations must be identical, the hook must start
    # after the last of them, with no journal file on disk, and the archive must end the same.
    _fake_split(monkeypatch)
    monkeypatch.setattr(gpcapture, "_now", lambda: _iso(NOW))     # the new push's capture: stamped at NOW (young)
    events = []
    _journal_log(monkeypatch, str(sandbox["local_root"]), events)
    old = _old(ac.root)
    real_hook = main.after_push_command
    argv = ["prep_push_rep", PINNED, None, "COUNT", "2"]

    monkeypatch.setattr(main, "after_push_command", lambda *args: None)          # 1. no hook at all
    argv[2] = str(_fresh_title(sandbox, make_video, mock_device, old))
    _dispatch(argv)
    without_hook, events[:] = list(events), []
    entry_without = mvcommon.load_library()[PINNED]
    out_without = capsys.readouterr().out

    at_spawn = []

    def child(child_argv):
        events.append(("hook starts the check",))
        at_spawn.append(_journals(sandbox["local_root"]))
        print(SUMMARY, flush=True)
        return 0, False

    monkeypatch.setattr(main, "_cp_spawn", child)
    monkeypatch.setattr(main, "after_push_command", real_hook)                  # 2. the real hook
    _fresh_title(sandbox, make_video, mock_device, old)
    _dispatch(argv)

    kinds = [event[0] for event in without_hook]
    assert kinds.count("open") == 3 and kinds.count("ponr") == 1 and kinds.count("commit") == 3   # prep · push · replace
    assert events == without_hook + [("hook starts the check",)]       # identical records; the hook after the last op
    assert at_spawn == [[]]                                              # no journal on disk: no journal scope open
    assert mvcommon.load_library()[PINNED] == entry_without and entry_without["status"] == "archived"
    out = capsys.readouterr().out
    # the archive's own output unchanged, then the hook's: the old object is due; the new push's two chunks (pushed at
    # NOW) are named apart and never looked up (IMP-C25 acfix — until 2026-10-27 this line counted all 3)
    assert out == out_without + "\n".join([_header(1, newer=2), SUMMARY]) + "\n"


def test_a_push_rolled_back_before_any_upload_journals_the_same_and_never_reaches_the_check(
        ac, sandbox, mock_device, make_video, stub_tech_specs, monkeypatch, capsys):
    # cmd_push's pre-upload failure path (the split produced nothing): its journal is rolled back and it returns False —
    # with or without the hook the journal operations are identical, and the hook starts nothing.
    _fake_split(monkeypatch, works=False)
    monkeypatch.setattr(gpcapture, "_now", lambda: _iso(NOW))
    events = []
    _journal_log(monkeypatch, str(sandbox["local_root"]), events)
    old = _old(ac.root)
    real_hook = main.after_push_command
    runs = []
    for hook in (lambda *args: None, real_hook):
        monkeypatch.setattr(main, "after_push_command", hook)
        master = _fresh_title(sandbox, make_video, mock_device, old)
        assert main.cmd_prep(PINNED, str(master)) is True
        events[:] = []
        _dispatch(["push", PINNED, "COUNT", "2"])
        runs.append((list(events), capsys.readouterr().out, _journals(sandbox["local_root"])))

    (without_hook, out_without, left_without), (with_hook, out_with, left_with) = runs
    assert [event[0] for event in without_hook] == ["open", "record", "record", "rollback"]
    assert with_hook == without_hook and out_with == out_without and left_with == left_without == []
    assert ac.child.calls == []
    assert mvcommon.load_library()[PINNED]["status"] == "local_ready"


def test_the_web_consoles_push_actions_run_in_process_and_never_reach_the_hook(ac, monkeypatch):
    # webui/server.py runs push / prep_push_rep IN-PROCESS (main.cmd_* under redirect_stdout), never as a push-family
    # child process, so a web job never reaches the CLI dispatcher's hook: no Chrome, no line. That is why the PLAN's
    # conditional webui change (set MEDIAVAULT_WEB_JOB=1 in a spawned push-family child) does not apply.
    server = pytest.importorskip("webui.server")
    _save(_old(ac.root), _state(ac.root, AUTO, "archived"))
    hooks = []
    monkeypatch.setattr(main, "after_push_command", lambda *args: hooks.append(args))
    monkeypatch.setattr(main, "cmd_push", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "cmd_prep_push_rep", lambda *args, **kwargs: None)

    assert server.ACTION_TABLE["push"][0]({"id": AUTO}) is True
    assert server.ACTION_TABLE["prep_push_rep"][0]({"id": AUTO, "filepath": "x"}) is None

    assert hooks == [] and ac.child.calls == []


def test_the_web_console_starts_no_main_py_child():
    # If webui/server.py ever starts a Python child (a push-family command), that child must carry MEDIAVAULT_WEB_JOB=1
    # in its environment (PLAN Step 13b) so the auto-confirm only prints its reminder there — this pin points at that
    # rule. (Its open-folder endpoint's os.startfile opens Explorer on a vetted folder: no Python child, allowed.)
    with open(os.path.join(REPO, "webui", "server.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names} | {(node.module or "").split(".")[0] for node in ast.walk(tree)
                                             if isinstance(node, ast.ImportFrom)}
    assert not imported & {"subprocess", "multiprocessing"}, imported
    called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)}
    assert not called & {"Popen", "system", "popen", "spawnv", "spawnl", "execv", "execl",
                         "create_subprocess_exec", "create_subprocess_shell"}, called
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "executable"]


# ===========================================================================
# acfix — the auto-confirm looks up only the objects old enough (IMP-C25 follow-up, 2026-10-27)
# ===========================================================================
def test_the_auto_confirm_child_looks_up_only_the_old_objects_never_the_push_that_just_ran(ac, monkeypatch, capsys):
    # The user's finding (DECISIONS.md §1g): after a prep_push_rep_enrich of a 9-chunk movie, the auto-confirm looked up
    # the 9 chunks pushed minutes earlier. Here the REAL chain runs in-process: the auto-confirm's own argv through
    # main.py's dispatcher into the real cmd_check_photos, whose browser child (`mainfetch.py check --run <dir>`) is
    # played by a recorder of the objects it is handed to look up. TV1 is 30 h old, TV2's push time is unknown (old);
    # TV3 (6 min) and MOVIE (23.9 h) are younger than AUTO_CONFIRM_MIN_AGE_H.
    _save(_leaf(ac.root, TV1, hours_ago=30), _leaf(ac.root, TV2, push_time=False), _leaf(ac.root, TV3, hours_ago=0.1),
          _leaf(ac.root, MOVIE, hours_ago=23.9))
    handed = []

    def spawn(argv):
        if os.path.basename(argv[1]) == "main.py":           # the auto-confirm's child: main.py check_photos …
            try:
                _dispatch(argv[2:])
            except SystemExit as stop:
                return stop.code, False
            return 0, False
        run_dir = argv[argv.index("--run") + 1]              # the check's browser child: mainfetch.py check --run <dir>
        doc = main._cp_read_json(os.path.join(run_dir, "run.json"))
        handed.append((doc.get("min_age_h"), [o["manual_id"] for o in doc["objects"]]))
        main._cp_write_json(os.path.join(run_dir, "progress.json"), {"v": 1, "state": "complete", "reason": None})
        return 0, False

    monkeypatch.setattr(main, "_cp_spawn", spawn)

    assert main.maybe_auto_confirm() == "ran"

    assert handed == [(24, [TV1, TV2])]                       # only these are looked up — never TV3 or MOVIE
    out = capsys.readouterr().out.splitlines()
    assert out[0] == _header(2, newer=2)
    assert "  2 object(s) pushed less than 24 h ago skipped — checked at a later push" in out
    assert out[-1].startswith("🔎 check_photos pending: 2 object(s) — ")     # the check's summary: the 2 old ones
    still = {o["manual_id"] for o in mvcommon.unverified_objects(mvcommon.load_library(), NOW)}
    assert {TV3, MOVIE} <= still                              # the young ones wait for a later push
