"""IMP-C29 — the fetch harvester collects what it waited for, and says what it lost.

fetch_single_entry triggers the downloads, then watches the Downloads folder: 300 s at first,
and for as long as a .crdownload is active after that. Two defects surfaced on the 2026-10-01
real fetch of a 9.1 GB episode:

  1. Once the 300 s had passed, the loop could only leave through its Timeout branch, which
     sat BEFORE the scan of Downloads. A download that took longer than 5 minutes was therefore
     never collected by the attempt that started it: the next attempt downloaded the same file a
     second time (Chrome's history shows such a second round for two earlier titles, starting
     right after the first round finished), and in the last attempt the entry ended INCOMPLETE
     with the finished file sitting in Downloads.
  2. A download that Chrome started and then failed ("Failed - Network error"; Google Photos
     downloads cannot resume) was reported as "Timeout (No active downloads)", which reads as
     "it never started".

Under test: the harvester gives up only AFTER looking, and a timeout now says what happened to
the triggered files that never arrived. With nothing triggered, the transcript is byte-identical
to the pre-fix code (frozen oracle).

Hermetic: Downloads is a tmp folder (never the real one), mainfetch's `time` is a fake clock
whose sleep() only advances it and fires the scripted Chrome download events, and
trigger_download is a script. No browser, no real C:\\Media, no library I/O.

IMP-C25 Step 10 (docs/feature-fetch-datetime/PLAN.md Step 10 c, d, g) adds, with the same fakes:
today's transcripts frozen (captured from HEAD b1cff7d before any Step 10 edit) and replayed with the
kill switch off and on; the locator seam; harvest_downloads' hash-once rule (IMP-B10); learn-on-fetch
(the learned sidecar, redirected into tmp_path by the autouse _hermetic_gphotos_state); and the
one-retry rule for a lost download (IMP-C34) with its failed-list row. Libraries, where a test needs
one, come from `sandbox`.
"""
import contextlib
import hashlib
import pathlib
import types

import pytest

import gpweb
import mainfetch
import mvcommon

PAYLOAD_A = b"A" * 4096
PAYLOAD_B = b"B" * 4096


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


class _Clock:
    """Stand-in for mainfetch's `time` module: sleep() advances a fake clock and fires the
    download events that are due."""

    def __init__(self):
        self.now = 1000.0
        self.events = []

    def time(self):
        return self.now

    def sleep(self, seconds=0):
        self.now += seconds
        due = sorted((e for e in self.events if e[0] <= self.now), key=lambda e: e[0])
        self.events = [e for e in self.events if e[0] > self.now]
        for _, fire in due:
            fire()

    def after(self, seconds, fire):
        self.events.append((self.now + seconds, fire))


class _Chrome:
    """Chrome's side of a download, scripted. start() drops an 'Unconfirmed N.crdownload' into
    the fake Downloads folder. After `seconds` it becomes the finished file — or, with
    payload=None, just disappears, as a failed download that cannot resume does."""

    def __init__(self, folder, clock):
        self.folder, self.clock, self.count = folder, clock, 0

    def start(self, name, payload, seconds):
        self.count += 1
        partial = self.folder / f"Unconfirmed {self.count}.crdownload"
        partial.write_bytes(b"partial")

        def finish():
            partial.unlink()
            if payload is not None:
                (self.folder / name).write_bytes(payload)

        self.clock.after(seconds, finish)
        return True


@pytest.fixture()
def world(tmp_path, monkeypatch):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    clock = _Clock()
    monkeypatch.setattr(mainfetch, "SYSTEM_DOWNLOADS_FOLDER", str(folder))
    monkeypatch.setattr(mainfetch, "time", clock)
    assert str(tmp_path) in mainfetch.SYSTEM_DOWNLOADS_FOLDER   # never the real Downloads folder
    return types.SimpleNamespace(clock=clock, chrome=_Chrome(folder, clock), downloads=folder, tmp=tmp_path)


def _script(monkeypatch, script):
    """Replace trigger_download with script(query, index) -> truthy if a download was requested."""
    calls = []

    def _trigger(driver, query, index=0):
        calls.append((query, index))
        return bool(script(query, index))

    monkeypatch.setattr(mainfetch, "trigger_download", _trigger)
    return calls


def _whole_entry(tmp_path):
    folder = tmp_path / "title"
    folder.mkdir()
    return {"filename": "Show.S01E01.mkv", "short_id": "abc123", "folder_path": str(folder),
            "hash": _sha(PAYLOAD_A), "search_term": "Show.S01E01 [abc123].mkv"}


def _restored(entry, name):
    return pathlib.Path(entry["folder_path"]) / "restore" / name


TIMEOUT = "❌ Timeout (No active downloads)."
IN_PROGRESS_NOTE = "     A download was in progress, but 1 triggered file(s) never arrived."
NOTHING_NOTE = "     No download appeared for 1 triggered file(s)."


# ---------------------------------------------------------------------------
# 1. a download that outlives the 300 s base timeout is still collected
# ---------------------------------------------------------------------------
def test_a_download_longer_than_the_base_timeout_is_collected_by_its_own_attempt(world, monkeypatch, capsys):
    entry = _whole_entry(world.tmp)
    calls = _script(monkeypatch, lambda q, i: world.chrome.start("Show.S01E01 [abc123].mkv", PAYLOAD_A, 1100))

    mainfetch.fetch_single_entry(None, entry)

    out = capsys.readouterr().out
    assert calls == [("Show.S01E01.mkv", 0)]      # ONE trigger: the file is not downloaded a second time
    assert "Extending wait" in out                # it waited for the active download...
    assert TIMEOUT not in out                     # ...and did not give up when it finished
    assert "✅ MOVED: Show.S01E01.mkv" in out and "✅ ENTRY COMPLETE." in out
    assert _restored(entry, "Show.S01E01.mkv").read_bytes() == PAYLOAD_A
    assert list(world.downloads.iterdir()) == []


def test_a_slow_download_in_the_last_attempt_completes_the_entry(world, monkeypatch, capsys):
    # The real whole-file flow: ATTEMPT 1 (plain name) finds nothing, ATTEMPT 2 (pushed name) does.
    entry = _whole_entry(world.tmp)

    def script(query, index):
        if query == entry["search_term"]:
            return world.chrome.start("Show.S01E01 [abc123].mkv", PAYLOAD_A, 900)
        return False

    calls = _script(monkeypatch, script)

    mainfetch.fetch_single_entry(None, entry)

    out = capsys.readouterr().out
    assert calls == [("Show.S01E01.mkv", 0), ("Show.S01E01 [abc123].mkv", 0)]
    assert out.count(TIMEOUT) == 1                # ATTEMPT 1 only, where nothing was triggered
    assert "✅ ENTRY COMPLETE." in out and "ENTRY INCOMPLETE" not in out
    assert _restored(entry, "Show.S01E01.mkv").read_bytes() == PAYLOAD_A
    assert list(world.downloads.iterdir()) == []


def test_a_download_within_the_base_timeout_is_collected_as_before(world, monkeypatch, capsys):
    entry = _whole_entry(world.tmp)
    calls = _script(monkeypatch, lambda q, i: world.chrome.start("Show.S01E01 [abc123].mkv", PAYLOAD_A, 100))

    mainfetch.fetch_single_entry(None, entry)

    out = capsys.readouterr().out
    assert calls == [("Show.S01E01.mkv", 0)]
    assert "Extending wait" not in out and TIMEOUT not in out
    assert "✅ MOVED: Show.S01E01.mkv" in out and "✅ ENTRY COMPLETE." in out
    assert _restored(entry, "Show.S01E01.mkv").read_bytes() == PAYLOAD_A


# ---------------------------------------------------------------------------
# 2. a timeout says what happened to the triggered files that never arrived
# ---------------------------------------------------------------------------
def test_a_download_that_started_and_vanished_is_reported_as_such(world, monkeypatch, capsys):
    # The 2026-10-01 case: Chrome started the download, the transfer broke, nothing arrived.
    entry = _whole_entry(world.tmp)
    _script(monkeypatch, lambda q, i: world.chrome.start("unused", None, 60))

    mainfetch.fetch_single_entry(None, entry)

    out = capsys.readouterr().out
    assert out.count(TIMEOUT) == 2 and out.count(IN_PROGRESS_NOTE) == 2      # both attempts
    assert "chrome://downloads" in out and "cannot resume: re-run the fetch" in out
    assert NOTHING_NOTE not in out
    assert "❌ ENTRY INCOMPLETE." in out
    lines = out.splitlines()
    assert lines[lines.index("   " + TIMEOUT) + 1].startswith(IN_PROGRESS_NOTE)   # right under the timeout


def test_a_trigger_that_started_no_download_is_reported_as_such(world, monkeypatch, capsys):
    entry = _whole_entry(world.tmp)
    _script(monkeypatch, lambda q, i: True)       # "Triggered", but no .crdownload ever shows up

    mainfetch.fetch_single_entry(None, entry)

    out = capsys.readouterr().out
    assert out.count(TIMEOUT) == 2 and out.count(NOTHING_NOTE) == 2
    assert "If it is not listed, Shift+D started nothing." in out
    assert IN_PROGRESS_NOTE not in out
    assert "❌ ENTRY INCOMPLETE." in out


# Captured from fetch_single_entry BEFORE this change (same fakes) — do not edit to make a
# failing test pass: a diff here means today's output changed where nothing was triggered.
ORACLE_NOTHING_TRIGGERED = "\n".join([
    "",
    "🔹 PROCESSING: Show.S01E01.mkv (abc123)",
    "",
    "   === ATTEMPT 1 (1 files) ===",
    "   > Watching Downloads (Infinite wait if active)...",
    "",
    "   ❌ Timeout (No active downloads).",
    "",
    "   === ATTEMPT 2 (1 files) ===",
    "   > Watching Downloads (Infinite wait if active)...",
    "",
    "   ❌ Timeout (No active downloads).",
    "",
    "   ❌ ENTRY INCOMPLETE.",
    "",
])


def test_nothing_triggered_keeps_the_frozen_transcript(world, monkeypatch, capsys):
    entry = _whole_entry(world.tmp)
    calls = _script(monkeypatch, lambda q, i: False)      # "Not found" on both attempts

    mainfetch.fetch_single_entry(None, entry)

    assert capsys.readouterr().out == ORACLE_NOTHING_TRIGGERED
    assert calls == [("Show.S01E01.mkv", 0), ("Show.S01E01 [abc123].mkv", 0)]


# ---------------------------------------------------------------------------
# 3. chunks: one arrives, one is lost, the lost one is fetched again and collected late
# ---------------------------------------------------------------------------
def test_chunks_one_collected_one_lost_then_refetched_past_the_timeout(world, monkeypatch, capsys):
    folder = world.tmp / "movie"
    folder.mkdir()
    c1, c2 = "Movie [abc123].chunk.001.mkv", "Movie [abc123].chunk.002.mkv"
    entry = {"filename": "Movie.mkv", "short_id": "abc123", "folder_path": str(folder), "hash": "unused",
             "search_term": "Movie [abc123].mkv",
             "split_info": {"is_split": True, "total_chunks": 2,
                            "chunks": [{"filename": c1, "hash": _sha(PAYLOAD_A)},
                                       {"filename": c2, "hash": _sha(PAYLOAD_B)}]}}

    def script(query, index):
        if query == c1:
            return world.chrome.start(c1, PAYLOAD_A, 100)       # arrives in time
        if query == c2:
            return world.chrome.start(c2, None, 50)             # the transfer breaks
        return world.chrome.start(c2, PAYLOAD_B, 400)           # ATTEMPT 2: slower than the base timeout

    calls = _script(monkeypatch, script)

    mainfetch.fetch_single_entry(None, entry)

    out = capsys.readouterr().out
    assert calls == [(c1, 0), (c2, 0), ("Movie [abc123].mkv", 1)]     # only the lost chunk is fetched again
    assert out.count(TIMEOUT) == 1 and out.count(IN_PROGRESS_NOTE) == 1
    assert f"✅ MOVED: {c1}" in out and f"✅ MOVED: {c2}" in out
    assert "✅ ENTRY COMPLETE." in out
    assert _restored(entry, c1).read_bytes() == PAYLOAD_A
    assert _restored(entry, c2).read_bytes() == PAYLOAD_B
    assert list(world.downloads.iterdir()) == []


# ===========================================================================
# IMP-C25 Step 10 — today's transcripts, FROZEN
# ===========================================================================
# Captured from mainfetch at HEAD b1cff7d (before any Step 10 edit) by running exactly these scenario
# bodies with exactly these fakes. With no learned data, no lost download and no foreign file in
# Downloads, Step 10 must reproduce them byte for byte — with the exact-item kill switch off AND on.
# Do not edit an oracle to make a failing test pass: a diff means today's fetch output changed.
PAYLOAD_C, PAYLOAD_H = b"C" * 4096, b"D" * 4096

ORACLES = {
    'whole_attempt2': '\n🔹 PROCESSING: Show.S01E01.mkv (abc123)\n\n   === ATTEMPT 1 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   ❌ Timeout (No active downloads).\n\n   === ATTEMPT 2 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   > 🔎 Checking: Show.S01E01 [abc123].mkv\n\r  🔍 Show.S01E01 [abc123].mkv  [████████████████████████] 4.00 KB / 4.00 KB \n     ✅ MOVED: Show.S01E01.mkv\n\n   ✅ ENTRY COMPLETE.\n',
    'split_holder': '\n🔹 PROCESSING: Movie.mkv (abc123)\n   > Detected Split File (2 chunks)\n\n   === ATTEMPT 1 (3 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   > 🔎 Checking: Movie [abc123].holder.mkv\n\r  🔍 Movie [abc123].holder.mkv  [████████████████████████] 4.00 KB / 4.00 KB \n     ✅ MOVED: Movie [abc123].holder.mkv\n\n   > 🔎 Checking: Movie [abc123].chunk.001.mkv\n\r  🔍 Movie [abc123].chunk.001.mkv  [████████████████████████] 4.00 KB / 4.00 KB \n     ✅ MOVED: Movie [abc123].chunk.001.mkv\n\n   ❌ Timeout (No active downloads).\n\n   === ATTEMPT 2 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   > 🔎 Checking: Movie [abc123].chunk.002.mkv\n\r  🔍 Movie [abc123].chunk.002.mkv  [████████████████████████] 4.00 KB / 4.00 KB \n     ✅ MOVED: Movie [abc123].chunk.002.mkv\n\n   ✅ ENTRY COMPLETE.\n',
    'split_partly_staged': '\n🔹 PROCESSING: Movie.mkv (abc123)\n   > Detected Split File (2 chunks)\n\n   === ATTEMPT 1 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   > 🔎 Checking: Movie [abc123].chunk.002.mkv\n\r  🔍 Movie [abc123].chunk.002.mkv  [████████████████████████] 4.00 KB / 4.00 KB \n     ✅ MOVED: Movie [abc123].chunk.002.mkv\n\n   ✅ ENTRY COMPLETE.\n',
    'all_present': '\n🔹 PROCESSING: Show.S01E02.mkv (def456)\n   ✅ All files already exist.\n',
    'slow_whole_extending': '\n🔹 PROCESSING: Show.S01E01.mkv (abc123)\n\n   === ATTEMPT 1 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   ❌ Timeout (No active downloads).\n\n   === ATTEMPT 2 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n   ⏳ Timeout reached, but 1 files downloading. Extending wait...\r   ⏳ Timeout reached, but 1 files downloading. Extending wait...\r   ⏳ Timeout reached, but 1 files downloading. Extending wait...\r\n   > 🔎 Checking: Show.S01E01 [abc123].mkv\n\r  🔍 Show.S01E01 [abc123].mkv  [████████████████████████] 4.00 KB / 4.00 KB \n     ✅ MOVED: Show.S01E01.mkv\n\n   ✅ ENTRY COMPLETE.\n',
    'route_season_with_extra': "--- FETCH ROUTER: tv-en-2001-demo-s01 ---\n   > [Account] Profile for tv-en-2001-demo-s01: 'tv' (C:\\Media\\Utils\\ChromeProfile_TV)\n   > 📂 Season Map detected. Resolving children...\n   > 📋 Processing 2 items...\n   > 📎 + 1 extra(s) (--fetchExtras)\n\n🔹 PROCESSING: S01E01.mkv (aaa111)\n\n   === ATTEMPT 1 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   > 🔎 Checking: S01E01 [aaa111].mkv\n\r  🔍 S01E01 [aaa111].mkv  [████████████████████████] 4.00 KB / 4.00 KB \n     ✅ MOVED: S01E01.mkv\n\n   ✅ ENTRY COMPLETE.\n\n🔹 PROCESSING: S01E02.mkv (bbb222)\n\n   === ATTEMPT 1 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   ❌ Timeout (No active downloads).\n\n   === ATTEMPT 2 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   > 🔎 Checking: S01E02 [bbb222].mkv\n\r  🔍 S01E02 [bbb222].mkv  [████████████████████████] 4.00 KB / 4.00 KB \n     ✅ MOVED: S01E02.mkv\n\n   ✅ ENTRY COMPLETE.\n\n=== 📎 FETCHING 1 EXTRA(S) for tv-en-2001-demo-s01 ===\n\n🔹 PROCESSING: Trailer.mkv (e1e1e1)\n\n   === ATTEMPT 1 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   ❌ Timeout (No active downloads).\n\n   === ATTEMPT 2 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   > 🔎 Checking: Trailer [e1e1e1].mkv\n\r  🔍 Trailer [e1e1e1].mkv  [████████████████████████] 4.00 KB / 4.00 KB \n     ✅ MOVED: Trailer.mkv\n\n   ✅ ENTRY COMPLETE.\n\n✅ Batch Processing Complete.\n",
}

ORACLE_CALLS = {
    "whole_attempt2": [("Show.S01E01.mkv", 0), ("Show.S01E01 [abc123].mkv", 0)],
    "split_holder": [("Movie [abc123].chunk.001.mkv", 0), ("Movie [abc123].chunk.002.mkv", 0),
                     ("Movie [abc123].holder.mkv", 0), ("Movie [abc123].mkv", 1)],
    "split_partly_staged": [("Movie [abc123].chunk.002.mkv", 0)],
    "all_present": [],
    "slow_whole_extending": [("Show.S01E01.mkv", 0), ("Show.S01E01 [abc123].mkv", 0)],
    "route_season_with_extra": [("S01E01.mkv", 0), ("S01E02.mkv", 0), ("S01E02 [bbb222].mkv", 0),
                                ("Trailer.mkv", 0), ("Trailer [e1e1e1].mkv", 0)],
}


def _scenario_whole_attempt2(world, monkeypatch, sandbox=None):
    entry = _whole_entry(world.tmp)
    calls = _script(monkeypatch, lambda q, i: world.chrome.start("Show.S01E01 [abc123].mkv", PAYLOAD_A, 100)
                    if q == entry["search_term"] else False)
    mainfetch.fetch_single_entry(None, entry)
    return calls


def _movie_entry(world, chunks=2, holder=True):
    folder = world.tmp / "movie"
    folder.mkdir(exist_ok=True)
    names = [f"Movie [abc123].chunk.00{n}.mkv" for n in range(1, chunks + 1)]
    payloads = [PAYLOAD_A, PAYLOAD_B, PAYLOAD_C][:chunks]
    split = {"is_split": True, "total_chunks": chunks,
             "chunks": [{"filename": n, "hash": _sha(p)} for n, p in zip(names, payloads)]}
    if holder:
        split["carried_out_tracks"] = [{"holder_filename": "Movie [abc123].holder.mkv", "holder_hash": _sha(PAYLOAD_H)}]
    return {"filename": "Movie.mkv", "short_id": "abc123", "folder_path": str(folder), "hash": "unused",
            "search_term": "Movie [abc123].mkv", "split_info": split}


def _scenario_split_holder(world, monkeypatch, sandbox=None):
    entry = _movie_entry(world)
    c1, c2, h = "Movie [abc123].chunk.001.mkv", "Movie [abc123].chunk.002.mkv", "Movie [abc123].holder.mkv"

    def script(q, i):
        if q == c1:
            return world.chrome.start(c1, PAYLOAD_A, 100)
        if q == h:
            return world.chrome.start(h, PAYLOAD_H, 80)
        if q == "Movie [abc123].mkv" and i == 1:
            return world.chrome.start(c2, PAYLOAD_B, 50)
        return False

    calls = _script(monkeypatch, script)
    mainfetch.fetch_single_entry(None, entry)
    return calls


def _scenario_split_partly_staged(world, monkeypatch, sandbox=None):
    entry = _movie_entry(world, holder=False)
    c1, c2 = "Movie [abc123].chunk.001.mkv", "Movie [abc123].chunk.002.mkv"
    (world.tmp / "movie" / "restore").mkdir(parents=True)
    (world.tmp / "movie" / "restore" / c1).write_bytes(PAYLOAD_A)
    calls = _script(monkeypatch, lambda q, i: world.chrome.start(c2, PAYLOAD_B, 30) if q == c2 else False)
    mainfetch.fetch_single_entry(None, entry)
    return calls


def _scenario_all_present(world, monkeypatch, sandbox=None):
    folder = world.tmp / "title"
    (folder / "restore").mkdir(parents=True)
    (folder / "restore" / "Show.S01E02.mkv").write_bytes(PAYLOAD_A)
    entry = {"filename": "Show.S01E02.mkv", "short_id": "def456", "folder_path": str(folder),
             "hash": _sha(PAYLOAD_A), "search_term": "Show.S01E02 [def456].mkv"}
    calls = _script(monkeypatch, lambda q, i: True)
    mainfetch.fetch_single_entry(None, entry)
    return calls


def _scenario_slow_whole_extending(world, monkeypatch, sandbox=None):
    entry = _whole_entry(world.tmp)
    calls = _script(monkeypatch, lambda q, i: world.chrome.start("Show.S01E01 [abc123].mkv", PAYLOAD_A, 320)
                    if q == entry["search_term"] else False)
    mainfetch.fetch_single_entry(None, entry)
    return calls


SEASON = "tv-en-2001-demo-s01"


def _seed_demo_season(sandbox, world, extra=True):
    folder = world.tmp / "Series" / "Demo" / "Season 01"
    folder.mkdir(parents=True)
    ep1, ep2 = SEASON + "e01", SEASON + "e02"
    library = {SEASON: {"type": "season_map", "folder_path": str(folder), "total_episodes": 2, "children": [ep1, ep2]},
               ep1: {"filename": "S01E01.mkv", "folder_path": str(folder), "hash": _sha(PAYLOAD_A), "parent_id": SEASON,
                     "status": "archived", "uploaded": True, "short_id": "aaa111", "search_term": "S01E01 [aaa111].mkv"},
               ep2: {"filename": "S01E02.mkv", "folder_path": str(folder), "hash": _sha(PAYLOAD_B), "parent_id": SEASON,
                     "status": "archived", "uploaded": True, "short_id": "bbb222", "search_term": "S01E02 [bbb222].mkv"}}
    if extra:
        library[SEASON]["extras"] = {"groups": {"Specials": {"items": [{
            "filename": "Trailer.mkv", "sub_rel": "Trailer.mkv", "hash": _sha(PAYLOAD_C), "uploaded": True,
            "status": "archived", "short_id": "e1e1e1", "search_term": "Trailer [e1e1e1].mkv"}]}}}
    mvcommon.save_library(library)
    return folder


def _route_stubs(monkeypatch):
    events = []

    class _Driver:
        def quit(self):
            events.append("quit")

    def _init(profile_key="movies"):
        events.append(("init", profile_key))
        return _Driver()

    monkeypatch.setattr(mainfetch, "init_driver", _init)
    monkeypatch.setattr(mainfetch, "fetch_session_lock", lambda *a, **k: contextlib.nullcontext())
    return events


def _scenario_route_season_with_extra(world, monkeypatch, sandbox=None):
    _seed_demo_season(sandbox, world)

    def script(q, i):
        if q == "S01E01.mkv":
            return world.chrome.start("S01E01 [aaa111].mkv", PAYLOAD_A, 60)
        if q == "S01E02 [bbb222].mkv":
            return world.chrome.start("S01E02 [bbb222].mkv", PAYLOAD_B, 70)
        if q == "Trailer [e1e1e1].mkv":
            return world.chrome.start("Trailer [e1e1e1].mkv", PAYLOAD_C, 40)
        return False

    calls = _script(monkeypatch, script)
    events = _route_stubs(monkeypatch)
    mainfetch.cmd_fetch_route(SEASON, fetch_extras=True, temp_dir=str(world.tmp / "T"))
    assert events == [("init", "tv"), "quit"]
    return calls


SCENARIOS = {"whole_attempt2": _scenario_whole_attempt2, "split_holder": _scenario_split_holder,
             "split_partly_staged": _scenario_split_partly_staged, "all_present": _scenario_all_present,
             "slow_whole_extending": _scenario_slow_whole_extending,
             "route_season_with_extra": _scenario_route_season_with_extra}


@pytest.mark.parametrize("enabled", [False, True], ids=["kill-switch-off", "kill-switch-on"])
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_todays_transcript_and_trigger_sequence_are_frozen(world, monkeypatch, capsys, sandbox, name, enabled):
    monkeypatch.setattr(mainfetch, "EXACT_LOCATOR_ENABLED", enabled, raising=False)
    calls = SCENARIOS[name](world, monkeypatch, sandbox)
    assert capsys.readouterr().out == ORACLES[name]
    assert calls == ORACLE_CALLS[name]
    assert list(world.downloads.iterdir()) == []
