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
"""
import hashlib
import pathlib
import types

import pytest

import mainfetch

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
