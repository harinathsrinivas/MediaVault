"""IMP-C25 Step 13b — auto-confirm unverified objects at the end of the next push (the OD-6 ruling; docs/feature-fetch-
datetime/PLAN.md P-11, P-19, Step 13b).

  13b.a  maybe_auto_confirm: the config switch, nothing pending, the web marker, only young pushes (the age boundary,
         an unknown push time), the Chrome profiles of the accounts the old objects route to, the fetch lock (held ·
         stale), the exact child command line through main._cp_spawn (the check's streaming idiom), the child failing /
         interrupted / not startable and any other error -> ONE warning line; it never raises

Hermetic: the sandbox library (tests/conftest.py), a fixed clock (mvcommon._now_utc), a runner standing in for
main._cp_spawn (tests/conftest.py's _hermetic_check_child refuses the real child — a child process would see none of a
test's patches: the REAL library, a REAL Chrome), the fetch lock moved under tmp_path by `real_auto_confirm`. No
network, no browser, no adb, no real C:\\Media, no real ~/.mediavault. Synthetic ids only.
"""
import hashlib
import json
import os
import sys
import time
import types
from datetime import datetime, timedelta, timezone

import pytest

import main
import mainfetch
import mvcommon

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
TV1, TV2, TV3 = "tv-en-2020-acshow-s01e01", "tv-en-2020-acshow-s01e02", "tv-en-2020-acshow-s01e03"
MOVIE = "mov-en-2021-acmovie"
SPORT = "oth-en-2022-acmatch"
CHILD_ARGV_TAIL = ["check_photos", "pending", "--apply", "--max-minutes", "15"]
SUMMARY = "🔎 check_photos pending: 2 object(s) — confirmed 2 · applied: 2 object(s) updated, 2 newly confirmed"


def _header(n):
    """The auto-confirm's own line before the child runs."""
    return (f"🔎 {n} pushed object(s) await Google Photos confirmation — confirming them now (check_photos pending "
            f"--apply, at most 15 min; to turn this off: \"auto_confirm_after_push\": false in mvconfig.json)")


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
    assert ac.child.calls == [[sys.executable, abs_main, *CHILD_ARGV_TAIL]]      # PLAN Step 13b: exactly this argv
    assert main.auto_confirm_argv() == ac.child.calls[0]
    # one line of its own, then the child's streamed output — whose last line is the check's summary
    assert capsys.readouterr().out.splitlines() == [_header(2), SUMMARY]


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


def test_one_old_object_is_enough_and_the_check_covers_every_pending_one(ac, capsys):
    _save(_leaf(ac.root, TV1, hours_ago=50), _leaf(ac.root, TV2, hours_ago=1), _leaf(ac.root, TV3, hours_ago=0.1))

    assert main.maybe_auto_confirm() == "ran"
    assert capsys.readouterr().out.splitlines()[0] == _header(3)


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
