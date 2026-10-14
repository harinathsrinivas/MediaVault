"""IMP-C25 Step 14 — what a fetch could not fetch: the identification block and `--manual` (docs/feature-fetch-datetime/
PLAN.md Step 14 + "Follow-ups carried into Step 14" a; P-5, P-12, P-13, P-18; the design decisions of
.candidates/c25-step14/STATUS.md).

  14.c  the identification block — account, exact uploaded name, the taken time as Google Photos shows it, content
        key, size, what was tried, the failed-list status and the commands that resolve it — for every object an entry
        part did not fetch, the `failed` ones included; only for a real Chrome (incoming / fallback mode: a stand-in
        driver keeps today's transcript). `--manual`: the block, the session's Photos tab at the object's best start
        (open_best_start — nothing clicked or downloaded), a watch of the entry's download folder for up to
        MANUAL_WAIT_S per object (no keyboard), only a download whose SHA-256 is an object's own kept (a wrong one
        deleted, the wait goes on), learned with source manual — a photo_id only when the item opened for it holds
        exactly those bytes — the failed ones taken too (follow-up a), statuses restored after a wait, Ctrl+C, the
        usage lines.

Hermetic: the fake Google Photos world (tests/gphotos_fake.py) through test_locator's `flow` harness; mainfetch's and
gprpc's clocks are fake, so a 30-minute wait takes no time and the user's own download is a clock event; every state dir,
~/Downloads and the process table are redirected (tests/conftest.py). Synthetic ids only (every photo id contains
"FAKE").
"""
import hashlib
import os

import pytest

import gpweb
import main
import mainfetch
import mvcommon
from test_locator import (SEASON, TAKEN, WEB_MARKERS, _bytes, _capture, _journal_objects, _obj, _one_episode,  # noqa: F401
                          _only_read_only_rpcids_ever, _split, _up, _whole, clock, flow)
from test_map_photos import _main_guard

EP = "tv-en-2015-demo-s04e01"
NAME, SID = "Demo.S04E01.mkv", "l4l4l4"


# ================================================================================ harness
def _episode_folder(sandbox):
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 04"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _hidden(flow, sandbox, data=None, capture=False):
    """A whole-file episode EP whose bytes `data` live in the TV account under ANOTHER name and taken time — no stage
    can find them (a legacy object; with `capture`, its push-time capture names the exact uploaded name and the
    SHA-1, and RPC is off, so the hash lookup cannot find them either). -> (folder, the hidden item)."""
    folder = _episode_folder(sandbox)
    data = data if data is not None else _bytes("hidden-episode")
    item = flow.world.add_item("tv", "Renamed.By.Hand.mkv", data=data, taken="2021-01-01T00:00:00Z")
    if capture:
        _capture(folder, SID, [_obj(data, _up(NAME, SID))])
        flow.world.wiz_token_missing["tv"] = True
    mvcommon.save_library({EP: _whole(folder, NAME, data, SID)})
    return folder, item


def _user_downloads(flow, item, account="tv"):
    """The user, in the automation Chrome: the item's ⋮ → Download. The browser writes it into the folder Chrome
    saves to — the fake's own download writer; no MediaVault code runs."""
    def act():
        flow.drivers[account]._write_download(item.filename, item.data, "user", item.photo_id)
    return act


def _when_opened(monkeypatch, clock, *events):
    """Schedule the user's actions — `events` = (seconds, act) … — from the moment --manual opened the first object's
    best start (the user starts looking then). -> the (what, photo_id, record) of every best start opened."""
    real, pending, opened = mainfetch.open_best_start, list(events), []

    def best_start(*args, **kwargs):
        result = real(*args, **kwargs)
        opened.append(result)
        while pending:
            seconds, act = pending.pop(0)
            clock.after(seconds, act)
        return result

    monkeypatch.setattr(mainfetch, "open_best_start", best_start)
    return opened


def _harvests(monkeypatch, clock):
    """Record every harvest round: {timeout, took (fake seconds), timed_out}."""
    rounds, real = [], mainfetch.harvest_downloads

    def harvest(queue, ctx, timeout=300, stop=None):
        start = clock.now
        res = real(queue, ctx, timeout=timeout, stop=stop)
        rounds.append({"timeout": timeout, "took": clock.now - start, "timed_out": res["timed_out"]})
        return res

    monkeypatch.setattr(mainfetch, "harvest_downloads", harvest)
    return rounds


@pytest.fixture(autouse=True)
def _no_keyboard(monkeypatch):
    """--manual reads nothing from the keyboard (PLAN Step 14: no stdin): any input() fails the test."""
    def refuse(*args, **kwargs):
        raise AssertionError("the fetch read the keyboard")
    monkeypatch.setattr("builtins.input", refuse)


def _block(out, title):
    """The lines of the identification block that starts with `title` (up to the next blank line)."""
    lines = out.splitlines()
    start = lines.index(title)
    end = next((i for i in range(start + 1, len(lines)) if not lines[i].strip()), len(lines))
    return lines[start:end]


# ===========================================================================
# 14.c — the identification block (no --manual)
# ===========================================================================
def test_an_object_nothing_fetched_gets_its_identification_block(flow, sandbox, capsys):
    # OD-5: a content-keyed object whose every candidate is another item — skipped, failed list (s4-skipped).
    folder = _episode_folder(sandbox)
    data = _bytes("the-real-bytes")
    other = flow.world.add_item("tv", _up(NAME, SID), data=_bytes("an-older-upload"), taken=TAKEN)
    _capture(folder, SID, [_obj(data, _up(NAME, SID))])
    mvcommon.save_library({EP: _whole(folder, NAME, data, SID)})

    mainfetch.cmd_fetch_route(EP)

    out = capsys.readouterr().out
    sha1 = hashlib.sha1(data).hexdigest()
    key = gpweb.dedup_key(sha1)
    [row] = mvcommon.read_failed()
    tried = " · ".join(f"{t['approach']} {t['outcome']}({t['n']})" for t in row["tried"])
    assert _block(out, f"   🧭 {NAME} · {EP} (whole)") == [
        f"   🧭 {NAME} · {EP} (whole)",
        "      account       tv",
        f"      uploaded as   {_up(NAME, SID)}",
        "      taken         2022-02-05 19:49:46 GMT+08:00 (the time Google Photos shows)",
        f"      content key   dedupKey {key} · SHA-1 {sha1}",
        f"      size          {mvcommon.human_readable_size(len(data))} ({len(data):,} bytes)",
        f"      tried         {tried}",
        "      failed list   s4-skipped — added by this run",
        f"      next          python main.py fetch {EP} --manual",
        "                      the browser opens where it most likely is; download it there — only its own bytes are kept",
        f"                    python main.py resolve_photos --id {EP}",
        "                      paste its photo URL or a search term, or mark it not uploaded"]
    header = "   === NOT FETCHED (1 file(s)) — what to look for in Google Photos ==="
    assert out.index("added to the failed list") < out.index(header) < out.index("❌ ENTRY INCOMPLETE.")
    block = _block(out, f"   🧭 {NAME} · {EP} (whole)")
    assert not any(m in line for line in block for m in WEB_MARKERS)          # P-13
    assert other.photo_id not in out and "FAKE" not in "\n".join(block)      # the block names no item
    assert "MANUAL" not in out and flow.world.downloads == []                 # nothing waits, nothing downloads
    web = pytest.importorskip("webui.server")
    assert web._parse_progress(out) == {"done": 0, "total": 1}               # the web progress is unchanged


def test_a_found_object_whose_download_failed_is_named_for_a_rerun(flow, sandbox, capsys):
    # Located (S0a), but neither its link nor Shift+D (nor the click path) started a download: no failed-list row —
    # a re-run fetches it; the block says so and offers --manual only.
    folder, item, episode = _one_episode(flow, sandbox, direct_mode="html", shift_d_requires_click=True)
    for query in (item.filename, "2022-02-05"):
        flow.world.script_search("tv", query, [])

    mainfetch.cmd_fetch_route(episode)

    out = capsys.readouterr().out
    block = _block(out, f"   🧭 Demo.S01E01.mkv · {episode} (whole)")
    assert "      failed list   not listed — its item was found, only its download failed: a re-run fetches it" in block
    assert f"      next          python main.py fetch {episode} --manual" in block
    assert not any("resolve_photos" in line for line in block) and mvcommon.read_failed() == []
    assert "      tried         photo_id exactly-one(1)" in block


def test_the_next_command_repeats_the_tempdir_and_fetches_an_extra_with_its_title():
    loc = {"manual_id": "mov-en-2019-film"}
    assert mainfetch._fetch_command(loc, {}, None) == "python main.py fetch mov-en-2019-film --manual"
    assert mainfetch._fetch_command(loc, {"group_rel": "Featurettes"}, r"D:\My Temp") == (
        r'python main.py fetch mov-en-2019-film --fetchExtras tempdir "D:\My Temp" --manual')


def test_a_stand_in_driver_keeps_todays_transcript_even_with_manual(flow, sandbox, capsys):
    # Legacy mode — a driver without Chrome's DevTools protocol (never a real Chrome): no block, no wait.
    folder, _item = _hidden(flow, sandbox)
    os.makedirs(mainfetch.SYSTEM_DOWNLOADS_FOLDER, exist_ok=True)
    ctx = mainfetch.FetchContext(manual=True)
    ctx.start_session("tv")

    mainfetch.fetch_single_entry(None, mvcommon.load_library()[EP], entry_id=EP, ctx=ctx)

    out = capsys.readouterr().out
    assert "❌ ENTRY INCOMPLETE." in out
    assert "🧭" not in out and "NOT FETCHED" not in out and "MANUAL" not in out


def test_everything_fetched_prints_no_block(flow, sandbox, capsys):
    folder, item, episode = _one_episode(flow, sandbox)

    mainfetch.cmd_fetch_route(episode, manual=True)

    out = capsys.readouterr().out
    assert "✅ ENTRY COMPLETE." in out and "🧭" not in out and "MANUAL" not in out


# ===========================================================================
# 14.c — --manual
# ===========================================================================
def test_manual_keeps_the_users_download_once_its_sha256_matches(flow, sandbox, capsys, monkeypatch, clock):
    folder, hidden = _hidden(flow, sandbox)
    opened = _when_opened(monkeypatch, clock, (420, _user_downloads(flow, hidden)))
    rounds = _harvests(monkeypatch, clock)

    mainfetch.cmd_fetch_route(EP, manual=True)

    out = capsys.readouterr().out
    assert (folder / "restore" / NAME).read_bytes() == hidden.data
    assert "✅ ENTRY COMPLETE." in out and "   > ✋ Manual: 1 of 1 file(s) fetched by hand." in out
    assert f"   > ✋ {NAME}: fetched by hand — its SHA-256 matched; recorded as a manual find." in out
    assert [what for what, _pid, _rec in opened] == ["the search for its exact uploaded name"]
    assert "      opened        the search for its exact uploaded name" in out
    manual = rounds[-1]
    assert manual["timeout"] == mainfetch.MANUAL_WAIT_S and not manual["timed_out"]
    assert 420 <= manual["took"] < mainfetch.MANUAL_WAIT_S                  # it ended with the download
    [learned] = mvcommon.read_learned("tv")
    assert (learned["source"], learned["proof"], learned["dedup_key"]) == (
        "manual", "sha256", gpweb.dedup_key(hashlib.sha1(hidden.data).hexdigest()))   # the bytes' content key
    assert "photo_id" not in learned                                        # it opened no item for it
    assert [r["via"] for r in flow.world.downloads] == ["user"]             # MediaVault requested nothing
    [row] = mvcommon.read_failed()                                          # the failed list is append-only
    assert row["reason"] == "not-found"
    assert [o["state"] for o in _journal_objects()] == ["moved"]


def test_manual_waits_thirty_minutes_then_gives_up_and_the_object_stays_failed(flow, sandbox, capsys, monkeypatch,
                                                                              clock):
    folder, _hidden_item = _hidden(flow, sandbox)
    rounds = _harvests(monkeypatch, clock)

    mainfetch.cmd_fetch_route(EP, manual=True)

    out = capsys.readouterr().out
    manual = rounds[-1]
    assert manual["timeout"] == mainfetch.MANUAL_WAIT_S == 1800
    assert manual["timed_out"] and manual["took"] >= mainfetch.MANUAL_WAIT_S
    assert f"   > ⏱️ {NAME}: no download with its bytes within 30 min." in out
    assert "   > ✋ Manual: 0 of 1 file(s) fetched by hand." in out and "❌ ENTRY INCOMPLETE." in out
    assert not (folder / "restore" / NAME).exists()
    assert [o["state"] for o in _journal_objects()] == ["failed"]           # its status came back after the wait
    assert mvcommon.read_learned("tv") == []


def test_a_wrong_download_is_deleted_and_the_wait_goes_on(flow, sandbox, capsys, monkeypatch, clock):
    folder, hidden = _hidden(flow, sandbox)
    stranger = flow.world.add_item("tv", "Someone.Else.mkv", data=_bytes("someone-else"), taken=TAKEN)
    _when_opened(monkeypatch, clock, (60, _user_downloads(flow, stranger)), (900, _user_downloads(flow, hidden)))

    mainfetch.cmd_fetch_route(EP, manual=True)

    out = capsys.readouterr().out
    assert "     🗑️ Wrong item downloaded (deleted): Someone.Else.mkv" in out
    assert out.index("Wrong item downloaded (deleted): Someone.Else.mkv") < out.index(f"✅ MOVED: {NAME}")
    assert (folder / "restore" / NAME).read_bytes() == hidden.data and "✅ ENTRY COMPLETE." in out
    assert sorted(os.listdir(folder / "restore")) == [NAME]                 # nothing else left behind


def test_manual_takes_the_failed_ones_too(flow, sandbox, capsys, monkeypatch, clock):
    # Follow-up a: the locator booked it `failed` (s4-skipped: RPC off, every candidate today's search could click is
    # another item) — --manual still waits for it.
    folder, hidden = _hidden(flow, sandbox, capture=True)
    flow.world.add_item("tv", _up(NAME, SID), data=_bytes("an-older-upload"), taken=TAKEN)
    _when_opened(monkeypatch, clock, (30, _user_downloads(flow, hidden)))

    mainfetch.cmd_fetch_route(EP, manual=True)

    out = capsys.readouterr().out
    [row] = mvcommon.read_failed()
    assert row["reason"] == "s4-skipped" and "today's search is skipped (OD-5)" in out
    block = _block(out, f"   🧭 [1/1] {NAME} · {EP} (whole)")
    assert "      failed list   s4-skipped — added by this run" in block
    assert "      opened        the search for its day (2022-02-05)" in block
    assert (folder / "restore" / NAME).read_bytes() == hidden.data and "✅ ENTRY COMPLETE." in out


def test_a_download_of_another_missing_object_of_the_entry_is_kept_during_the_wait(flow, sandbox, capsys, monkeypatch,
                                                                                    clock):
    # A split entry, both chunks hidden: while chunk 1 is waited for, the user downloads chunk 2 first — kept, not
    # deleted as a wrong item; chunk 2's own turn then has nothing left to wait for.
    folder = sandbox["local_root"] / "Movies" / "Pair (2018)"
    folder.mkdir(parents=True)
    datas = [_bytes("pair-chunk-1"), _bytes("pair-chunk-2")]
    hidden = [flow.world.add_item("movies", f"Renamed part {n}.mkv", data=d, taken="2021-01-01T00:00:00Z")
              for n, d in enumerate(datas, 1)]
    mvcommon.save_library({"mov-en-2018-pair": _split(folder, "Pair.2018.mkv", "p2p2p2", datas)})
    opened = _when_opened(monkeypatch, clock, (60, _user_downloads(flow, hidden[1], "movies")),
                          (240, _user_downloads(flow, hidden[0], "movies")))

    mainfetch.cmd_fetch_route("mov-en-2018-pair", manual=True)

    out = capsys.readouterr().out
    for n, data in enumerate(datas, 1):
        assert (folder / "restore" / f"Pair.2018 [p2p2p2].chunk.00{n}.mkv").read_bytes() == data
    assert "Wrong item downloaded" not in out and len(opened) == 1          # chunk 2 needed no turn of its own
    assert "   > ✋ Pair.2018 [p2p2p2].chunk.002.mkv: already fetched while waiting for another file." in out
    assert "   > ✋ Manual: 2 of 2 file(s) fetched by hand." in out and "✅ ENTRY COMPLETE." in out
    assert [(r["label"], r["source"]) for r in mvcommon.read_learned("movies")] == [
        ("chunk 2/2", "manual"), ("chunk 1/2", "manual")]


def test_each_wait_ends_as_soon_as_its_own_object_arrived(flow, sandbox, capsys, monkeypatch, clock):
    # Chunk 1 arrives while chunk 2 is still missing: its wait ends at once (not 30 min later), chunk 2 gets its own
    # turn and its own 30 minutes.
    folder = sandbox["local_root"] / "Movies" / "Pair (2018)"
    folder.mkdir(parents=True)
    datas = [_bytes("pair-chunk-1"), _bytes("pair-chunk-2")]
    hidden = [flow.world.add_item("movies", f"Renamed part {n}.mkv", data=d, taken="2021-01-01T00:00:00Z")
              for n, d in enumerate(datas, 1)]
    mvcommon.save_library({"mov-en-2018-pair": _split(folder, "Pair.2018.mkv", "p2p2p2", datas)})
    opened = _when_opened(monkeypatch, clock, (60, _user_downloads(flow, hidden[0], "movies")))
    rounds = _harvests(monkeypatch, clock)

    mainfetch.cmd_fetch_route("mov-en-2018-pair", manual=True)

    out = capsys.readouterr().out
    first, second = [r for r in rounds if r["timeout"] == mainfetch.MANUAL_WAIT_S]
    assert not first["timed_out"] and 60 <= first["took"] < 120
    assert second["timed_out"] and second["took"] >= mainfetch.MANUAL_WAIT_S and len(opened) == 2
    assert (folder / "restore" / "Pair.2018 [p2p2p2].chunk.001.mkv").read_bytes() == datas[0]
    assert "   > ⏱️ Pair.2018 [p2p2p2].chunk.002.mkv: no download with its bytes within 30 min." in out
    assert "   > ✋ Manual: 1 of 2 file(s) fetched by hand." in out and "❌ ENTRY INCOMPLETE." in out


def test_the_located_items_page_is_opened_and_its_photo_id_learned(flow, sandbox, capsys, monkeypatch, clock):
    # Located by its learned photo_id (S0a), but no download started (its link answered a page; Shift+D is ignored on a
    # directly opened page and no search lists it): --manual opens its item page, the user downloads it there.
    folder, item, episode = _one_episode(flow, sandbox, direct_mode="html", shift_d_requires_click=True)
    for query in (item.filename, "2022-02-05"):
        flow.world.script_search("tv", query, [])
    opened = _when_opened(monkeypatch, clock, (90, _user_downloads(flow, item)))

    mainfetch.cmd_fetch_route(episode, manual=True)

    out = capsys.readouterr().out
    assert [(what, pid) for what, pid, _rec in opened] == [("its item page (S0a found it)", item.photo_id)]
    assert (folder / "restore" / "Demo.S01E01.mkv").read_bytes() == item.data
    [learned] = mvcommon.read_learned("tv")
    assert (learned["photo_id"], learned["proof"], learned["source"]) == (item.photo_id, "sha256", "manual")
    assert learned["approach"] == {"tried": "s0_photo_id", "outcome": "exactly-one", "n": 1, "via": "rpc"}
    # P-5: a manual find is confirmed by a later check, never by itself (source manual)
    block, _changed, conflict = mvcommon.apply_learned(None, learned, "tv")
    assert conflict is None and block["accounts"]["tv"]["photo_id"] == item.photo_id
    assert "confirmed" not in block["accounts"]["tv"]


def test_an_opened_item_holding_other_bytes_is_never_learned(flow, sandbox, capsys, monkeypatch, clock):
    # A legacy object pinned by its exact NAME to an item that serves other bytes (inconsistent — IMP-C27): --manual
    # opens that item, the user downloads the right bytes from another one. The download is kept (SHA-256), but the
    # opened item is NOT recorded as its photo_id — only an item holding exactly the verified bytes may be.
    folder = _episode_folder(sandbox)
    data = _bytes("the-library-bytes")
    named = flow.world.add_item("tv", _up(NAME, SID), data=_bytes("a-transcode"), taken=TAKEN)
    right = flow.world.add_item("tv", "Renamed.By.Hand.mkv", data=data, taken="2021-01-01T00:00:00Z")
    mvcommon.save_library({EP: _whole(folder, NAME, data, SID)})
    opened = _when_opened(monkeypatch, clock, (45, _user_downloads(flow, right)))

    mainfetch.cmd_fetch_route(EP, manual=True)

    out = capsys.readouterr().out
    [row] = mvcommon.read_failed()
    assert row["reason"] == "inconsistent"
    assert [(what, pid) for what, pid, _rec in opened] == [("its item page (S2 found it)", named.photo_id)]
    assert (folder / "restore" / NAME).read_bytes() == data and "✅ ENTRY COMPLETE." in out
    [learned] = mvcommon.read_learned("tv")
    assert (learned["source"], learned["proof"]) == ("manual", "sha256") and "photo_id" not in learned
    assert named.photo_id not in str(learned) and right.photo_id not in str(learned)


def test_manual_in_the_downloads_fallback_watches_downloads(flow, sandbox, capsys, monkeypatch, clock):
    # Chrome refused the download folder (P-12 fallback): the user's download lands in ~/Downloads — kept from there.
    folder, hidden = _hidden(flow, sandbox)
    flow.world.refuse_download_redirect = True
    monkeypatch.setattr(mainfetch, "SYSTEM_DOWNLOADS_FOLDER", flow.world.default_download_dir)
    os.makedirs(flow.world.default_download_dir, exist_ok=True)
    _when_opened(monkeypatch, clock, (120, _user_downloads(flow, hidden)))

    mainfetch.cmd_fetch_route(EP, manual=True)

    out = capsys.readouterr().out
    assert "Chrome refused the download folder (CDP)" in out
    assert f"   > Downloads are saved into {flow.world.default_download_dir} and kept only when" in out
    assert (folder / "restore" / NAME).read_bytes() == hidden.data and "✅ ENTRY COMPLETE." in out
    assert [r["source"] for r in mvcommon.read_learned("tv")] == ["manual"]


def test_a_best_start_that_cannot_be_opened_still_waits_for_the_user(flow, sandbox, capsys, monkeypatch, clock):
    # The tab --manual wants to open crashes: the wait still runs — the user can look for the file by hand.
    from selenium.common.exceptions import WebDriverException
    folder, hidden = _hidden(flow, sandbox)
    real_round, real_tab = mainfetch._manual_round, mainfetch.use_photos_tab

    def broken(driver):
        raise WebDriverException("fake: the tab crashed")

    def round_with_a_broken_tab(*args, **kwargs):  # the stages ran with a working tab; only --manual's page breaks
        monkeypatch.setattr(mainfetch, "use_photos_tab", broken)
        clock.after(75, _user_downloads(flow, hidden))
        try:
            return real_round(*args, **kwargs)
        finally:
            monkeypatch.setattr(mainfetch, "use_photos_tab", real_tab)

    monkeypatch.setattr(mainfetch, "_manual_round", round_with_a_broken_tab)

    mainfetch.cmd_fetch_route(EP, manual=True)

    out = capsys.readouterr().out
    assert ("      opened        nothing — the page could not be opened (WebDriverException); look for it yourself"
            in out)
    assert (folder / "restore" / NAME).read_bytes() == hidden.data and "✅ ENTRY COMPLETE." in out


def test_ctrl_c_during_the_wait_stops_the_fetch(flow, sandbox, capsys, monkeypatch, clock):
    folder, _hidden_item = _hidden(flow, sandbox)

    def ctrl_c():
        raise KeyboardInterrupt

    _when_opened(monkeypatch, clock, (100, ctrl_c))

    mainfetch.cmd_fetch_route(EP, manual=True)

    out = capsys.readouterr().out
    assert "🛑 Stopped by user." in out and "⏱️" not in out and "Manual:" not in out
    assert not (folder / "restore" / NAME).exists()
    assert not (folder / "restore" / mainfetch.INCOMING_DIR_NAME).exists()  # the interrupted run cleaned up


# ===========================================================================
# 14.c — the command lines name --manual (follow-up a)
# ===========================================================================
def test_the_fetch_usage_lines_name_manual(capsys):
    with pytest.raises(SystemExit):
        mainfetch.parse_fetch_args(["mainfetch.py"])
    assert capsys.readouterr().out.strip() == (
        "Usage: fetch [id] [episodes] [range] [tempdir <path>] [--fetchExtras] [--manual]")
    for argv, line in ((["main.py"], "  fetch_restore [id] [OPT: episodes 1-3] [tempdir <path>] [--fetchExtras] "
                                     "[--manual]"),
                       (["main.py"], "  fetch [id] [tempdir <path>] [--manual]"),
                       (["main.py", "fetch"], "❌ Usage: fetch [id] [OPT: episodes 1-3] [tempdir <path>] [--fetchExtras] "
                                              "[--manual]"),
                       (["main.py", "fetch_restore"], "❌ Usage: fetch_restore [id] [OPT: episodes 1-3] "
                                                      "[tempdir <path>] [--fetchExtras] [--manual]")):
        import sys as real_sys

        class Sys:
            def __getattr__(self, name):
                return getattr(real_sys, name)

        fake = Sys()
        fake.argv = argv
        with pytest.raises(SystemExit):
            exec(_main_guard(), dict(main.__dict__, sys=fake))
        assert line in capsys.readouterr().out.splitlines()
