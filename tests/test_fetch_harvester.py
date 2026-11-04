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
    monkeypatch.setattr(mainfetch, "EXACT_LOCATOR_ENABLED", enabled)
    calls = SCENARIOS[name](world, monkeypatch, sandbox)
    assert capsys.readouterr().out == ORACLES[name]
    assert calls == ORACLE_CALLS[name]
    assert list(world.downloads.iterdir()) == []


# ---------------------------------------------------------------------------
# 10.d — the locator seam and the kill switch (P-14)
# ---------------------------------------------------------------------------
def test_the_exact_item_stages_are_on_by_default_and_the_seam_is_a_no_op():
    assert mainfetch.EXACT_LOCATOR_ENABLED is True
    queue = [{"filename": "x.mkv", "hash": "h", "status": "pending", "locator": {}}]
    before = [dict(i) for i in queue]
    assert mainfetch._run_locator_phase(None, queue, mainfetch.FetchContext()) is None
    assert queue == before


@pytest.mark.parametrize("enabled, runs", [(True, 1), (False, 0)])
def test_the_seam_runs_before_todays_attempts_only_when_enabled(world, monkeypatch, enabled, runs):
    monkeypatch.setattr(mainfetch, "EXACT_LOCATOR_ENABLED", enabled)
    seen = []
    calls = _script(monkeypatch, lambda q, i: seen.append(("trigger", q)) or False)

    def seam(driver, queue, ctx):
        seen.append(("seam", driver, [i["locator"]["label"] for i in queue], ctx.run_id))

    monkeypatch.setattr(mainfetch, "_run_locator_phase", seam)
    ctx = mainfetch.FetchContext()
    mainfetch.fetch_single_entry("DRIVER", _whole_entry(world.tmp), ctx=ctx)

    assert [e for e in seen if e[0] == "seam"] == [("seam", "DRIVER", ["whole"], ctx.run_id)] * runs
    if runs:
        assert seen[0][0] == "seam"                        # before the first legacy trigger
    assert calls == [("Show.S01E01.mkv", 0), ("Show.S01E01 [abc123].mkv", 0)]   # today's attempts, unchanged


def test_objects_the_seam_settles_skip_todays_attempts(world, monkeypatch, capsys):
    # Step 12's contract: an object it fetched is 'done', one it must not hand to the legacy attempts (OD-5) is
    # 'failed'; only what is still 'pending' gets today's attempts.
    entry = _movie_entry(world)
    c1, h = "Movie [abc123].chunk.001.mkv", "Movie [abc123].holder.mkv"

    def seam(driver, queue, ctx):
        restore = pathlib.Path(queue[0]["dest"])
        (restore / c1).write_bytes(PAYLOAD_A)
        queue[0]["status"] = "done"                         # fetched by an exact-item stage
        queue[1]["status"] = "failed"                       # every candidate proven different (OD-5)

    monkeypatch.setattr(mainfetch, "_run_locator_phase", seam)
    calls = _script(monkeypatch, lambda q, i: world.chrome.start(h, PAYLOAD_H, 30) if q == h else False)

    mainfetch.fetch_single_entry(None, entry)

    out = capsys.readouterr().out
    assert calls == [(h, 0)]                                # neither the done nor the failed chunk is searched
    assert "=== ATTEMPT 1 (1 files) ===" in out and "ATTEMPT 2" not in out
    assert f"✅ MOVED: {h}" in out and "Timeout" not in out   # nothing pending: the round ends at once
    assert "❌ ENTRY INCOMPLETE." in out                    # the failed chunk keeps the entry incomplete


# ---------------------------------------------------------------------------
# 10.c — harvest_downloads: every file hashed once per run (IMP-B10), learn-on-fetch
# ---------------------------------------------------------------------------
WEB_MARKERS = ("🔹 PROCESSING:", "Detected Split File", "✅ MOVED:", "✅ ENTRY COMPLETE.")   # PLAN P-13
EP_ID = "tv-en-2001-show-s01e01"
PHOTO = "AF1QipFAKEstep10harvest01"   # synthetic photo id


def _session(account):
    ctx = mainfetch.FetchContext()
    ctx.start_session(account)
    return ctx


def _count_hashes(monkeypatch):
    """Record every file the harvester hashes (the real hash runs: cached_sha1 must be filled as in production)."""
    names = []

    def counting(path, *a, **k):
        names.append(pathlib.Path(path).name)
        return mvcommon.calculate_file_hash(path, *a, **k)

    monkeypatch.setattr(mainfetch, "calculate_file_hash", counting)
    return names


def _key(payload):
    return gpweb.dedup_key(hashlib.sha1(payload).hexdigest())


# Captured at HEAD b1cff7d: a foreign .mkv sits in Downloads for the whole run — today it is hashed and named in
# BOTH attempts. IMP-B10 (absorbed by IMP-C25 Step 10) deliberately drops the second, identical check.
TODAY_FOREIGN_FILE = '\n🔹 PROCESSING: Show.S01E01.mkv (abc123)\n\n   === ATTEMPT 1 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   > 🔎 Checking: Holiday.mkv\n\r  🔍 Holiday.mkv  [████████████████████████] 4.00 KB / 4.00 KB \n\n   ❌ Timeout (No active downloads).\n\n   === ATTEMPT 2 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   > 🔎 Checking: Holiday.mkv\n\r  🔍 Holiday.mkv  [████████████████████████] 4.00 KB / 4.00 KB \n\n   ❌ Timeout (No active downloads).\n\n   ❌ ENTRY INCOMPLETE.\n'
FOREIGN_CHECK = '\n   > 🔎 Checking: Holiday.mkv\n\r  🔍 Holiday.mkv  [████████████████████████] 4.00 KB / 4.00 KB \n'


def test_a_foreign_file_is_hashed_once_per_run_not_once_per_attempt(world, monkeypatch, capsys):
    (world.downloads / "Holiday.mkv").write_bytes(b"F" * 4096)
    hashed = _count_hashes(monkeypatch)
    _script(monkeypatch, lambda q, i: False)

    mainfetch.fetch_single_entry(None, _whole_entry(world.tmp))

    assert hashed == ["Holiday.mkv"]
    first = TODAY_FOREIGN_FILE.index(FOREIGN_CHECK)
    second = TODAY_FOREIGN_FILE.index(FOREIGN_CHECK, first + 1)
    assert capsys.readouterr().out == TODAY_FOREIGN_FILE[:second] + TODAY_FOREIGN_FILE[second + len(FOREIGN_CHECK):]
    assert (world.downloads / "Holiday.mkv").read_bytes() == b"F" * 4096   # a foreign file is never touched


def test_a_file_met_in_one_entry_completes_a_later_one_without_rehashing(world, monkeypatch, capsys, sandbox):
    # S01E01's blind ATTEMPT 2 click downloads S01E02's item: no match for E01, so it stays in Downloads — and
    # completes E02 when E02's harvest meets it, without hashing it again (today it was re-hashed per attempt).
    _seed_demo_season(sandbox, world, extra=False)
    hashed = _count_hashes(monkeypatch)
    calls = _script(monkeypatch, lambda q, i: world.chrome.start("S01E02 [bbb222].mkv", PAYLOAD_B, 30)
                    if q == "S01E01 [aaa111].mkv" else False)
    _route_stubs(monkeypatch)

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert hashed == ["S01E02 [bbb222].mkv"]
    assert out.count("🔎 Checking: S01E02 [bbb222].mkv") == 2       # hashed under E01, named again where it matches
    assert out.count("✅ MOVED: S01E02.mkv") == 1 and "MOVED: S01E01.mkv" not in out
    assert calls == [("S01E01.mkv", 0), ("S01E01 [aaa111].mkv", 0), ("S01E02.mkv", 0)]
    # It arrived during this session, so it also teaches the session's account about E02.
    assert [(r["manual_id"], r["label"]) for r in mvcommon.read_learned("tv")] == [(SEASON + "e02", "whole")]


def test_a_verified_download_teaches_the_session_account_silently(world, monkeypatch, capsys):
    ctx = _session("tv")
    entry = _whole_entry(world.tmp)
    _script(monkeypatch, lambda q, i: world.chrome.start("Show.S01E01 [abc123].mkv", PAYLOAD_A, 100)
            if q == entry["search_term"] else False)

    mainfetch.fetch_single_entry(None, entry, entry_id=EP_ID, ctx=ctx)

    assert capsys.readouterr().out == ORACLES["whole_attempt2"]      # learning prints nothing
    [record] = mvcommon.read_learned("tv")
    assert {k: v for k, v in record.items() if k != "at"} == {
        "v": 1, "account": "tv", "manual_id": EP_ID, "label": "whole", "sha256": _sha(PAYLOAD_A),
        "dedup_key": _key(PAYLOAD_A), "proof": "sha256", "source": "fetch"}
    assert ctx.stats["learned"] == 1
    assert [mvcommon.read_learned(a) for a in ("movies", "anime", "others")] == [[], [], []]


@pytest.mark.parametrize("same_bytes", [True, False], ids=["record-holds-these-bytes", "record-holds-other-bytes"])
def test_a_photo_id_is_learned_only_when_the_trigger_record_holds_these_bytes(world, monkeypatch, same_bytes):
    # Step 12's trigger record names the item it downloaded; the bytes that arrived decide whether it was right.
    ctx = _session("movies")
    record_key = _key(PAYLOAD_A if same_bytes else PAYLOAD_B)
    stage = {"tried": "s0_photo_id", "outcome": "exactly-one", "n": 1, "via": "rpc"}

    def seam(driver, queue, ctx):
        queue[0]["_trigger"] = {"method": "direct", "approach": stage, "record": {
            "photo_id": PHOTO, "dedup_key": record_key, "taken_ms": 1644061786000, "tz_offset_ms": 28800000,
            "upload_ms": 1700000000000, "width": 1920, "height": 1080, "filename": "Show.S01E01 [abc123].mkv"}}

    monkeypatch.setattr(mainfetch, "_run_locator_phase", seam)
    entry = _whole_entry(world.tmp)
    _script(monkeypatch, lambda q, i: world.chrome.start("Show.S01E01 [abc123].mkv", PAYLOAD_A, 50)
            if q == entry["search_term"] else False)

    mainfetch.fetch_single_entry(None, entry, entry_id=EP_ID, ctx=ctx)

    [record] = mvcommon.read_learned("movies")
    assert record["dedup_key"] == _key(PAYLOAD_A)                     # always the verified bytes' key
    if same_bytes:
        assert {k: record[k] for k in ("photo_id", "taken_ms", "tz_offset_ms", "upload_ms", "width", "height",
                                       "gp_filename", "approach")} == {
            "photo_id": PHOTO, "taken_ms": 1644061786000, "tz_offset_ms": 28800000, "upload_ms": 1700000000000,
            "width": 1920, "height": 1080, "gp_filename": "Show.S01E01 [abc123].mkv", "approach": stage}
    else:
        assert not {"photo_id", "taken_ms", "upload_ms", "gp_filename", "approach"} & set(record)


def test_a_file_already_in_downloads_is_moved_but_proves_nothing_about_this_account(world, monkeypatch, capsys):
    # e.g. a finished download an earlier run (perhaps in another account's session) left behind
    (world.downloads / "Show.S01E01 [abc123].mkv").write_bytes(PAYLOAD_A)
    ctx = _session("tv")
    calls = _script(monkeypatch, lambda q, i: False)

    mainfetch.fetch_single_entry(None, _whole_entry(world.tmp), entry_id=EP_ID, ctx=ctx)

    out = capsys.readouterr().out
    assert "✅ MOVED: Show.S01E01.mkv" in out and "✅ ENTRY COMPLETE." in out
    assert calls == [("Show.S01E01.mkv", 0)]
    assert mvcommon.read_learned() == [] and ctx.stats["learned"] == 0


@pytest.mark.parametrize("session, entry_id", [(None, EP_ID), ("tv", None)], ids=["no-session", "no-owning-id"])
def test_nothing_is_learned_without_a_session_account_or_an_owning_id(world, monkeypatch, session, entry_id):
    ctx = _session(session) if session else mainfetch.FetchContext()
    entry = _whole_entry(world.tmp)
    _script(monkeypatch, lambda q, i: world.chrome.start("Show.S01E01 [abc123].mkv", PAYLOAD_A, 10)
            if q == entry["search_term"] else False)

    mainfetch.fetch_single_entry(None, entry, entry_id=entry_id, ctx=ctx)

    assert mvcommon.read_learned() == []


def test_a_learned_sidecar_failure_warns_once_and_the_fetch_goes_on(world, monkeypatch, capsys):
    def boom(account, record):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(mvcommon, "append_learned", boom)
    entry = _whole_entry(world.tmp)
    _script(monkeypatch, lambda q, i: world.chrome.start("Show.S01E01 [abc123].mkv", PAYLOAD_A, 10)
            if q == entry["search_term"] else False)

    mainfetch.fetch_single_entry(None, entry, entry_id=EP_ID, ctx=_session("tv"))

    out = capsys.readouterr().out
    warning = ("     ⚠️ Not recorded in the learned sidecar (OSError: [Errno 28] No space left on device) — "
               "the file itself is fetched.")
    assert out.count(warning) == 1
    assert "✅ MOVED: Show.S01E01.mkv" in out and "✅ ENTRY COMPLETE." in out
    assert not any(marker in warning for marker in WEB_MARKERS)
    assert _restored(entry, "Show.S01E01.mkv").read_bytes() == PAYLOAD_A


@pytest.mark.parametrize("enabled", [True, False], ids=["kill-switch-on", "kill-switch-off"])
def test_every_chunk_and_holder_is_learned_under_its_own_label(world, monkeypatch, enabled):
    monkeypatch.setattr(mainfetch, "EXACT_LOCATOR_ENABLED", enabled)   # P-14: learning stays on either way
    ctx = _session("movies")
    entry = _movie_entry(world)
    c1, c2, h = "Movie [abc123].chunk.001.mkv", "Movie [abc123].chunk.002.mkv", "Movie [abc123].holder.mkv"
    payload = {c1: PAYLOAD_A, c2: PAYLOAD_B, h: PAYLOAD_H}
    _script(monkeypatch, lambda q, i: world.chrome.start(q, payload[q], 20) if q in payload else False)

    mainfetch.fetch_single_entry(None, entry, entry_id="mov-en-2020-movie", ctx=ctx)

    learned = {r["label"]: r for r in mvcommon.read_learned("movies")}
    assert set(learned) == {"chunk 1/2", "chunk 2/2", "holder"}
    for label, data in (("chunk 1/2", PAYLOAD_A), ("chunk 2/2", PAYLOAD_B), ("holder", PAYLOAD_H)):
        assert (learned[label]["sha256"], learned[label]["dedup_key"]) == (_sha(data), _key(data))
        assert learned[label]["manual_id"] == "mov-en-2020-movie"


def test_an_extra_is_learned_under_its_title_and_extras_label(world, monkeypatch, sandbox):
    _seed_demo_season(sandbox, world)
    [extra] = mainfetch.resolve_title_extras(SEASON)
    _script(monkeypatch, lambda q, i: world.chrome.start("Trailer [e1e1e1].mkv", PAYLOAD_C, 10)
            if q == "Trailer [e1e1e1].mkv" else False)

    mainfetch.fetch_single_entry(None, extra, ctx=_session("tv"))

    [record] = mvcommon.read_learned("tv")
    assert (record["manual_id"], record["label"], record["sha256"]) == (SEASON, "extra Specials/Trailer.mkv",
                                                                         _sha(PAYLOAD_C))


def test_what_a_fetch_learned_routes_the_next_fetch(world, monkeypatch, sandbox, capsys):
    # End to end through the apply engine: an episode fetched in the MOVIES account's session (an X-Files-like
    # object) teaches its block that it lives there; the next fetch routes it to movies without any override.
    import main
    folder = world.tmp / "Series" / "Show" / "Season 01"
    folder.mkdir(parents=True)
    mvcommon.save_library({EP_ID: {"filename": "Show.S01E01.mkv", "short_id": "abc123", "folder_path": str(folder),
                                   "hash": _sha(PAYLOAD_A), "search_term": "Show.S01E01 [abc123].mkv",
                                   "status": "archived", "uploaded": True}})
    _script(monkeypatch, lambda q, i: world.chrome.start("Show.S01E01 [abc123].mkv", PAYLOAD_A, 10)
            if q == "Show.S01E01 [abc123].mkv" else False)
    mainfetch.fetch_single_entry(None, mvcommon.load_library()[EP_ID], entry_id=EP_ID, ctx=_session("movies"))

    report = main.merge_learned_gphotos()

    assert report["status"] == "saved"
    block = mvcommon.load_library()[EP_ID]["gphotos"]
    assert block["home_account"] == "movies" and block["dedup_key"] == _key(PAYLOAD_A)
    assert block["accounts"]["movies"]["verified_by"] == "sha256" and "confirmed" not in block["accounts"]["movies"]
    [item] = mainfetch.build_fetch_queue(mvcommon.load_library()[EP_ID], str(folder / "fresh"), entry_id=EP_ID)
    assert item["locator"]["route"] == {"account": "movies", "reason": "learned"}


def test_harvest_downloads_reports_how_its_round_ended(world, monkeypatch, capsys):
    queue = [{"filename": "x.mkv", "hash": _sha(PAYLOAD_A), "dest": str(world.tmp), "status": "pending",
              "triggered": False}]
    assert mainfetch.harvest_downloads(queue, mainfetch.FetchContext()) == {
        "timed_out": True, "saw_download": False, "unmatched": 0}
    queue[0]["status"] = "done"
    assert mainfetch.harvest_downloads(queue, mainfetch.FetchContext()) == {
        "timed_out": False, "saw_download": False, "unmatched": 0}
    assert capsys.readouterr().out.count("❌ Timeout (No active downloads).") == 1


# ---------------------------------------------------------------------------
# 10.g — IMP-C34: a lost download is requested once more; a second loss lands on the failed list
# ---------------------------------------------------------------------------
RETRY_1 = "   === RETRY (1 file(s) whose download was lost — requested once more) ==="
SHOW_TERM = "Show.S01E01 [abc123].mkv"


def _lost_twice(name, listed=True):
    tail = "added to the failed list; " if listed else ""
    return f"     ❌ Download lost twice: {name} — {tail}re-run the fetch to try again."


def test_a_lost_whole_file_is_requested_once_more_and_arrives(world, monkeypatch, capsys):
    # The live case: ATTEMPT 1 (the plain name) never finds a whole file; ATTEMPT 2 finds it and its transfer breaks.
    entry = _whole_entry(world.tmp)
    sent = []

    def script(q, i):
        if q != SHOW_TERM:
            return False
        sent.append(q)
        return world.chrome.start(SHOW_TERM, None if len(sent) == 1 else PAYLOAD_A, 60)   # the first one breaks

    calls = _script(monkeypatch, script)
    ctx = _session("tv")

    mainfetch.fetch_single_entry(None, entry, entry_id=EP_ID, ctx=ctx)

    out = capsys.readouterr().out
    assert calls == [("Show.S01E01.mkv", 0), (SHOW_TERM, 0), (SHOW_TERM, 0)]
    assert out.count(RETRY_1) == 1
    assert out.index(IN_PROGRESS_NOTE) < out.index(RETRY_1) < out.index("✅ MOVED: Show.S01E01.mkv")
    assert "✅ ENTRY COMPLETE." in out and "lost twice" not in out
    assert ctx.stats == {"learned": 1, "retried": 1, "recovered": 1, "lost_twice": 0}
    assert mvcommon.read_failed() == []
    assert _restored(entry, "Show.S01E01.mkv").read_bytes() == PAYLOAD_A


def test_a_second_loss_ends_the_object_and_lands_on_the_failed_list(world, monkeypatch, capsys, sandbox):
    entry = _whole_entry(world.tmp)
    mvcommon.save_library({EP_ID: dict(entry, status="archived", uploaded=True)})
    calls = _script(monkeypatch, lambda q, i: world.chrome.start("x", None, 60) if q == SHOW_TERM else False)
    ctx = _session("tv")

    mainfetch.fetch_single_entry(None, entry, entry_id=EP_ID, ctx=ctx)

    out = capsys.readouterr().out
    assert calls == [("Show.S01E01.mkv", 0), (SHOW_TERM, 0), (SHOW_TERM, 0)]     # one retry, never a third request
    assert out.count(RETRY_1) == 1 and out.count(_lost_twice("Show.S01E01.mkv")) == 1
    assert out.index(RETRY_1) < out.index(_lost_twice("Show.S01E01.mkv")) < out.index("❌ ENTRY INCOMPLETE.")
    [row] = mvcommon.read_failed()
    assert {k: v for k, v in row.items() if k != "at"} == {
        "run": ctx.run_id, "account": "tv", "category": "tv", "manual_id": EP_ID, "label": "whole",
        "sha256": _sha(PAYLOAD_A), "uploaded_name": SHOW_TERM, "reason": "download-lost"}
    assert ctx.stats == {"learned": 0, "retried": 1, "recovered": 0, "lost_twice": 1}
    # The Excel view the user works from names the reason in its status column.
    exported = mvcommon.export_failed_csv(mvcommon.load_library())
    assert exported["rows"] == 1
    assert "download-lost" in pathlib.Path(exported["path"]).read_text(encoding="utf-8-sig")


def test_a_chunk_lost_and_then_missed_by_attempt_2_is_requested_again_as_it_was_found(world, monkeypatch, capsys):
    entry = _movie_entry(world, holder=False)
    c1, c2 = "Movie [abc123].chunk.001.mkv", "Movie [abc123].chunk.002.mkv"
    sent = []

    def script(q, i):
        if q == c1:
            return world.chrome.start(c1, PAYLOAD_A, 20)
        if q == c2:
            sent.append(q)
            return world.chrome.start(c2, None if len(sent) == 1 else PAYLOAD_B, 40)
        return False                                        # ATTEMPT 2's blind fallback finds nothing

    calls = _script(monkeypatch, script)

    mainfetch.fetch_single_entry(None, entry)               # stand-alone: retried all the same, nothing recorded

    out = capsys.readouterr().out
    assert calls == [(c1, 0), (c2, 0), ("Movie [abc123].mkv", 1), (c2, 0)]
    assert out.count(RETRY_1) == 1 and f"✅ MOVED: {c2}" in out and "✅ ENTRY COMPLETE." in out
    assert mvcommon.read_failed() == []


def test_attempt_2_is_the_one_re_request_of_a_chunk_lost_in_attempt_1(world, monkeypatch, capsys):
    entry = _movie_entry(world, holder=False)
    c1, c2 = "Movie [abc123].chunk.001.mkv", "Movie [abc123].chunk.002.mkv"
    calls = _script(monkeypatch, lambda q, i: world.chrome.start(c1, PAYLOAD_A, 20) if q == c1
                    else world.chrome.start(c2, None, 40))  # c2's transfer breaks whoever requests it

    mainfetch.fetch_single_entry(None, entry, entry_id="mov-en-2020-movie", ctx=_session("movies"))

    out = capsys.readouterr().out
    assert calls == [(c1, 0), (c2, 0), ("Movie [abc123].mkv", 1)]           # never a third request for c2
    assert "RETRY" not in out and out.count(_lost_twice(c2)) == 1
    [row] = mvcommon.read_failed()
    assert (row["account"], row["manual_id"], row["label"], row["reason"]) == (
        "movies", "mov-en-2020-movie", "chunk 2/2", "download-lost")


def test_a_lost_holder_is_requested_again_by_its_name(world, monkeypatch, capsys):
    entry = _movie_entry(world)
    c1, c2, h = "Movie [abc123].chunk.001.mkv", "Movie [abc123].chunk.002.mkv", "Movie [abc123].holder.mkv"
    sent = []

    def script(q, i):
        if q in (c1, c2):
            return world.chrome.start(q, PAYLOAD_A if q == c1 else PAYLOAD_B, 20)
        if q == h:
            sent.append(q)
            return world.chrome.start(h, None if len(sent) == 1 else PAYLOAD_H, 30)
        return False

    calls = _script(monkeypatch, script)

    mainfetch.fetch_single_entry(None, entry)

    out = capsys.readouterr().out
    assert calls[-2:] == [("Movie [abc123].mkv", 0), (h, 0)]      # ATTEMPT 2 missed it; the retry asks by name
    assert out.count(RETRY_1) == 1 and f"✅ MOVED: {h}" in out and "✅ ENTRY COMPLETE." in out


def test_a_lost_extra_is_requested_once_more(world, monkeypatch, capsys, sandbox):
    _seed_demo_season(sandbox, world)
    [extra] = mainfetch.resolve_title_extras(SEASON)
    sent = []

    def script(q, i):
        if q != "Trailer [e1e1e1].mkv":
            return False
        sent.append(q)
        return world.chrome.start(q, None if len(sent) == 1 else PAYLOAD_C, 30)

    calls = _script(monkeypatch, script)
    ctx = _session("tv")

    mainfetch.fetch_single_entry(None, extra, ctx=ctx)

    out = capsys.readouterr().out
    assert calls == [("Trailer.mkv", 0), ("Trailer [e1e1e1].mkv", 0), ("Trailer [e1e1e1].mkv", 0)]
    assert out.count(RETRY_1) == 1 and "✅ MOVED: Trailer.mkv" in out
    assert (ctx.stats["retried"], ctx.stats["recovered"]) == (1, 1)


# Captured at HEAD b1cff7d: ATTEMPT 2's click downloads ANOTHER item (hash mismatch, left in Downloads).
TODAY_WRONG_ITEM = "\n🔹 PROCESSING: Show.S01E01.mkv (abc123)\n\n   === ATTEMPT 1 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   ❌ Timeout (No active downloads).\n\n   === ATTEMPT 2 (1 files) ===\n   > Watching Downloads (Infinite wait if active)...\n\n   > 🔎 Checking: Other [zzz999].mkv\n\r  🔍 Other [zzz999].mkv  [████████████████████████] 4.00 KB / 4.00 KB \n\n   ❌ Timeout (No active downloads).\n     A download was in progress, but 1 triggered file(s) never arrived. If chrome://downloads lists it as failed (e.g. 'Failed - Network error'), the transfer broke. Google Photos downloads cannot resume: re-run the fetch (files already fetched are skipped).\n\n   ❌ ENTRY INCOMPLETE.\n"


LOST_NOTE = ("     A download was in progress, but 1 triggered file(s) never arrived. If chrome://downloads lists it as "
             "failed (e.g. 'Failed - Network error'), the transfer broke. Google Photos downloads cannot resume: re-run "
             "the fetch (files already fetched are skipped).")


def _wrong_note(*names):
    return f"     Not a broken transfer: another item arrived instead ({', '.join(names)}) — it is not requested again."


def _not_fetched(name, listed=True):
    tail = "; added to the failed list (wrong-item)" if listed else ""
    return f"     ❌ Not fetched: {name} — the search picked another item{tail}."


def test_a_wrong_item_is_not_a_lost_download(world, monkeypatch, capsys):
    # The request worked — it fetched a different item. Requesting it again would download that item again.
    # [live check 2026-10-05] Today's transcript with its two deliberate changes: the round says another item arrived
    # (not the lost-download note, which sent the user to chrome://downloads for a transfer that never broke), and the
    # object ends on the failed list as wrong-item. Still no retry.
    calls = _script(monkeypatch, lambda q, i: world.chrome.start("Other [zzz999].mkv", PAYLOAD_B, 60)
                    if q == SHOW_TERM else False)
    ctx = _session("tv")

    mainfetch.fetch_single_entry(None, _whole_entry(world.tmp), entry_id=EP_ID, ctx=ctx)

    expected = (TODAY_WRONG_ITEM.replace(LOST_NOTE, _wrong_note("Other [zzz999].mkv"))
                .replace("\n\n   ❌ ENTRY INCOMPLETE.", f"\n{_not_fetched('Show.S01E01.mkv')}\n\n   ❌ ENTRY INCOMPLETE."))
    assert LOST_NOTE in TODAY_WRONG_ITEM and expected != TODAY_WRONG_ITEM
    assert capsys.readouterr().out == expected
    assert calls == [("Show.S01E01.mkv", 0), (SHOW_TERM, 0)] and ctx.stats["retried"] == 0
    [row] = mvcommon.read_failed()
    assert (row["account"], row["manual_id"], row["label"], row["sha256"], row["uploaded_name"], row["reason"]) == (
        "tv", EP_ID, "whole", _sha(PAYLOAD_A), SHOW_TERM, "wrong-item")
    assert row["tried"] == [{"approach": "legacy", "outcome": "wrong-item", "n": 1}] and row["run"] == ctx.run_id


def test_a_duplicate_is_not_a_lost_download(world, monkeypatch, capsys):
    # c2's transfer breaks; ATTEMPT 2's blind index then fetches c1's item again (a duplicate, deleted).
    entry = _movie_entry(world, holder=False)
    c1, c2 = "Movie [abc123].chunk.001.mkv", "Movie [abc123].chunk.002.mkv"

    def script(q, i):
        if q == c1:
            return world.chrome.start(c1, PAYLOAD_A, 20)
        if q == c2:
            return world.chrome.start(c2, None, 40)
        if q == "Movie [abc123].mkv" and i == 1:
            return world.chrome.start("Movie [abc123].chunk.001 (1).mkv", PAYLOAD_A, 30)
        return False

    calls = _script(monkeypatch, script)

    mainfetch.fetch_single_entry(None, entry, entry_id="mov-en-2020-movie", ctx=_session("movies"))

    out = capsys.readouterr().out
    assert "     ⚠️ Duplicate. Deleting." in out
    assert calls == [(c1, 0), (c2, 0), ("Movie [abc123].mkv", 1)]
    assert "RETRY" not in out and "lost twice" not in out
    assert "❌ ENTRY INCOMPLETE." in out
    # [live check 2026-10-05] c2's last request fetched another item (c1's): it ends on the failed list as wrong-item.
    [row] = mvcommon.read_failed()
    assert (row["label"], row["sha256"], row["reason"]) == ("chunk 2/2", _sha(PAYLOAD_B), "wrong-item")
    assert out.count(_not_fetched(c2)) == 1


def test_a_round_with_a_wrong_item_and_a_broken_transfer_says_both(world, capsys):
    # [live check 2026-10-05] Two requests in one round: one fetched another item, the other's transfer broke. The
    # round names what arrived instead and still reports the one file that never came; which request fetched the
    # wrong item cannot be told, so both requests are marked (no loss is booked — the IMP-C34 rule).
    queue = [{"filename": n, "hash": _sha(p), "dest": str(world.tmp), "status": "pending", "triggered": True}
             for n, p in (("a.mkv", PAYLOAD_A), ("b.mkv", PAYLOAD_B))]
    ctx = _session("tv")
    ctx.downloads = {}                                       # the session's snapshot: Downloads was empty
    world.chrome.start("Other [zzz999].mkv", b"X" * 4096, 30)
    world.chrome.start("ignored", None, 40)

    rnd = mainfetch.harvest_downloads(queue, ctx)

    out = capsys.readouterr().out
    assert rnd == {"timed_out": True, "saw_download": True, "unmatched": 1}
    assert out.index(_wrong_note("Other [zzz999].mkv")) < out.index(IN_PROGRESS_NOTE)   # 1 of the 2 never arrived
    mainfetch._note_losses(queue, rnd, ctx)
    assert [i.get("_wrong") for i in queue] == [1, 1] and not any(i.get("_losses") for i in queue)


def test_a_wrong_item_then_a_broken_transfer_still_gets_its_one_retry(world, monkeypatch, capsys):
    # ATTEMPT 1 fetched another item (no loss); ATTEMPT 2's transfer broke (a loss): IMP-C34 requests it once more and
    # it arrives — the object is fetched, so nothing is booked as wrong-item.
    entry = _whole_entry(world.tmp)
    sent = []

    def script(q, i):
        sent.append(q)
        if q == "Show.S01E01.mkv":
            return world.chrome.start("Other [zzz999].mkv", PAYLOAD_B, 30)
        return world.chrome.start(SHOW_TERM, None if len(sent) == 2 else PAYLOAD_A, 60)

    calls = _script(monkeypatch, script)
    ctx = _session("tv")

    mainfetch.fetch_single_entry(None, entry, entry_id=EP_ID, ctx=ctx)

    out = capsys.readouterr().out
    assert calls == [("Show.S01E01.mkv", 0), (SHOW_TERM, 0), (SHOW_TERM, 0)]
    assert out.index(_wrong_note("Other [zzz999].mkv")) < out.index(IN_PROGRESS_NOTE) < out.index(RETRY_1)
    assert "✅ ENTRY COMPLETE." in out and "Not fetched" not in out and mvcommon.read_failed() == []
    assert (ctx.stats["retried"], ctx.stats["recovered"]) == (1, 1)


def test_after_a_second_loss_no_later_stage_requests_the_object_again(world, monkeypatch, capsys):
    # Step 12's stages run before today's attempts (the seam): two lost requests there — a direct download, then
    # Shift+D — end the object for this run, so the legacy attempts and the retry round must leave it alone.
    entry = _whole_entry(world.tmp)
    stage_requests = []

    def seam(driver, queue, ctx):
        for method in ("direct", "shift_d"):
            mainfetch._fire(queue[0], lambda m=method: stage_requests.append(m) or world.chrome.start("x", None, 30))
            mainfetch._note_losses(queue, mainfetch.harvest_downloads(queue, ctx), ctx)

    monkeypatch.setattr(mainfetch, "_run_locator_phase", seam)
    calls = _script(monkeypatch, lambda q, i: True)
    ctx = _session("tv")

    mainfetch.fetch_single_entry(None, entry, entry_id=EP_ID, ctx=ctx)

    out = capsys.readouterr().out
    assert stage_requests == ["direct", "shift_d"]
    assert calls == [] and "ATTEMPT" not in out and "RETRY" not in out    # never requested a third time
    assert out.count(_lost_twice("Show.S01E01.mkv")) == 1 and "❌ ENTRY INCOMPLETE." in out
    [row] = mvcommon.read_failed()
    assert (row["manual_id"], row["reason"]) == (EP_ID, "download-lost")


def test_the_retry_starts_only_once_nothing_is_downloading(world, monkeypatch, capsys):
    entry = _whole_entry(world.tmp)
    stamps = []

    def script(q, i):
        if q != SHOW_TERM:
            return False
        stamps.append(world.clock.now)
        if len(stamps) == 1:
            return world.chrome.start("x", None, 700)       # still active well past the base timeout, then breaks
        return world.chrome.start(SHOW_TERM, PAYLOAD_A, 30)

    _script(monkeypatch, script)

    mainfetch.fetch_single_entry(None, entry)

    out = capsys.readouterr().out
    first, again = stamps
    assert again - first > 700                               # requested again only after the broken one was gone
    assert "Extending wait" in out and out.count(RETRY_1) == 1 and "✅ ENTRY COMPLETE." in out


def test_the_batch_ends_with_one_line_about_lost_downloads(world, monkeypatch, capsys, sandbox):
    _seed_demo_season(sandbox, world, extra=False)
    sent = []

    def script(q, i):
        if q == "S01E01 [aaa111].mkv":
            sent.append(q)
            return world.chrome.start(q, None if len(sent) == 1 else PAYLOAD_A, 30)   # breaks, then arrives
        if q == "S01E02 [bbb222].mkv":
            return world.chrome.start("x", None, 30)                                   # lost every time
        return False

    _script(monkeypatch, script)
    _route_stubs(monkeypatch)

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    line = "   > 🔁 Lost downloads: 2 requested once more (1 arrived); 1 lost twice — see the failed list."
    assert out.count(line) == 1 and out.index(line) < out.index("✅ Batch Processing Complete.")
    [row] = mvcommon.read_failed()
    assert (row["account"], row["manual_id"], row["reason"]) == ("tv", SEASON + "e02", "download-lost")
    for new in (line, RETRY_1, _lost_twice("S01E02.mkv")):          # P-13: no new line carries a web marker
        assert not any(marker in new for marker in WEB_MARKERS)
    web = pytest.importorskip("webui.server")
    assert web._parse_progress(out) == {"done": 1, "total": 2}


def test_download_lost_is_a_failed_list_reason():
    assert "download-lost" in mvcommon.GP_FAILED_REASONS
    assert mvcommon.GP_FAILED_REASONS[:9] == ("not-found", "ambiguous", "wrong-item", "s4-skipped", "probable",
                                              "missing-after-backup", "missing-everywhere", "partial-set",
                                              "inconsistent")             # P-18's reasons keep their order
