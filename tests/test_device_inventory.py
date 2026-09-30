"""IMP-C25 Step 8 — the READ-ONLY device cross-check (main.device_inventory + main.classify_device_objects).

Pinned here, because Step 9 must not call an object "missing from the cloud" without it:
  (a) the inventory of each configured phone — ONE `adb devices`, `find '<root>' -type f` with the
      /storage/emulated/0 fallback root, `stat -c '%s %Y %n'` in bounded batches — over CRLF output and names
      with spaces, apostrophes, shell metacharacters and unicode; placeholder serials, unplugged / offline /
      unauthorized phones, a missing adb binary, timeouts and failing commands are reported, never raised;
  (b) READ-ONLY: every adb argv any test here issues must pass THIS module's own allowlist (independent of
      main's guard), carry stdin=DEVNULL and a timeout; main's guard refuses every write shape and a refused
      argv never runs;
  (c) the per-object verdict on_device / freed / absent / unchecked over all four categories, split chunks,
      FLAC holders and extras (whole + split), with size agreement from the block, the capture or tech_spec;
  (d) a round trip through the REAL push code on the mock device: the names cmd_push, push_one_extra and
      write_remote_mvmeta put on the phone are exactly the names the classifier looks for.
The fake adb splits the command it is given the way the device's POSIX shell does, so a quoting mistake loses
the file (and an operator outside quotes fails the test) instead of passing silently. Synthetic data only: no
real adb, phone, C:\\Media or library file.
"""
import copy
import hashlib
import json
import os
import re
import subprocess
import types

import pytest

import main
import mvcommon

MOVIES, SERIES = "FAKEMOV0001", "FAKESER0002"
ALIASES = {"movies": MOVIES, "series": SERIES, "others": "<NEW_PIXEL_SERIAL>"}
ROOTS = ("/sdcard/Media/", "/storage/emulated/0/Media/")
STAT_FORMAT = "%s %Y %n"
# Names real libraries carry (spaces, apostrophes, unicode) and names built to break naive quoting.
HOSTILE = [
    "Series/Show/Season 02/Show.S02E02 [a1b2c3].mkv",
    "Movies/Sorcerer's Stone (2001)/Sorcerer's Stone [d4e5f6].mkv",
    "Movies/Amélie (2001)/Amélie [0a0b0c].mkv",
    "Anime/日本語 アニメ/日本語 アニメ - 01 [111111].mkv",
    'Sports/Odd/Quote "Double" $HOME `id` ; rm -rf x & y | z [222222].mkv',
    "Sports/Odd/Two  spaces and a trailing one [333333].mkv ",
    "Series/Show/Season 02/Show.S02E03 [444444].mvmeta.json",
]


# ============================================================================ the device shell's view

def device_words(command):
    """The words a POSIX device shell makes of `command` (adb joins `adb shell`'s arguments with spaces and
    hands the device shell that string) — for the subset a read-only listing may use: single quotes,
    backslash escapes, spaces. Any character the shell treats as an operator, expansion or glob OUTSIDE single
    quotes fails the test: it would run something else, or look for another file."""
    words, word, in_word, quoted, i = [], [], False, False, 0
    while i < len(command):
        c = command[i]
        if quoted:
            if c == "'":
                quoted = False
            else:
                word.append(c)
        elif c == "'":
            quoted = in_word = True
        elif c == "\\":
            i += 1
            assert i < len(command), f"dangling backslash in {command!r}"
            word.append(command[i])
            in_word = True
        elif c == " ":
            if in_word:
                words.append("".join(word))
                word, in_word = [], False
        elif c in ';|&<>()`$"*?[]{}~#!\t\r\n':
            raise AssertionError(f"shell metacharacter {c!r} outside quotes in device command {command!r}")
        else:
            word.append(c)
            in_word = True
        i += 1
    assert not quoted, f"unterminated quote in device command {command!r}"
    if in_word:
        words.append("".join(word))
    return words


def assert_readonly(argv):
    """THIS module's own read-only allowlist — deliberately independent of main._dev_argv_readonly: an adb
    call is `adb [-s <serial>] devices`, or `adb -s <serial> shell <cmd>` (a shell command always names its
    phone) where the device shell's words of <cmd> are `find <root> -type f` or
    `stat -c '%s %Y %n' <path> ...` with every path under a Media root. Returns the device words (['devices']
    for `adb devices`)."""
    assert isinstance(argv, list) and argv[:1] == ["adb"], f"not an adb call: {argv!r}"
    rest = argv[1:]
    pinned = rest[:1] == ["-s"]
    if pinned:
        assert len(rest) > 1 and re.fullmatch(r"[A-Za-z0-9._:-]+", rest[1]), f"bad serial in {argv!r}"
        rest = rest[2:]
    if rest == ["devices"]:
        return ["devices"]
    assert pinned, f"a shell command must name its phone with -s: {argv!r}"
    assert rest[:1] == ["shell"] and len(rest) > 1, f"not a read-only adb call: {argv!r}"
    words = device_words(" ".join(rest[1:]))
    if words[:1] == ["find"]:
        assert words[1:] in [[root, "-type", "f"] for root in ROOTS], f"not a read-only find: {words!r}"
    elif words[:1] == ["stat"]:
        assert words[1:3] == ["-c", STAT_FORMAT] and len(words) > 3, f"not a read-only stat: {words!r}"
        for path in words[3:]:
            assert path.startswith(ROOTS) and path not in ROOTS, f"stat outside the Media roots: {path!r}"
    else:
        raise AssertionError(f"device command off the read-only allowlist: {words!r}")
    return words


# ============================================================================ the fake adb

class Phone:
    """One fake phone. state: what `adb devices` lists (None = not attached). files: {path relative to the
    Media root: (size, mtime)} — the key may be bytes for a name that is not UTF-8. tree: a real directory
    standing for the Media root, read at call time (used instead of `files`). crlf: end output lines with
    CRLF like the real Pixels. symlink: the field-lesson quirk — `find '/sdcard/Media/'` lists nothing
    (exit 0). find: {root: "error" | "partial" | "timeout"}. stat: None | "fail" | "timeout" | "silent" for
    every stat call, or {n: mode} for the n-th stat call only (1-based)."""

    def __init__(self, state="device", files=None, tree=None, crlf=True, symlink=False, find=None, stat=None):
        self.state, self.files, self.tree, self.crlf = state, dict(files or {}), tree, crlf
        self.symlink, self.find_modes, self.stat_mode = symlink, dict(find or {}), stat
        self.stat_calls = 0

    def listing(self):
        if self.tree is None:
            return dict(self.files)
        out = {}
        for folder, _dirs, names in os.walk(self.tree):
            for name in names:
                full = os.path.join(folder, name)
                st = os.stat(full)
                out[os.path.relpath(full, self.tree).replace(os.sep, "/")] = (st.st_size, int(st.st_mtime))
        return out

    def output(self, lines):
        end = b"\r\n" if self.crlf else b"\n"
        return b"".join((line if isinstance(line, bytes) else line.encode("utf-8")) + end for line in lines)

    def find(self, root, argv):
        mode = self.find_modes.get(root)
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, main.DEVICE_FIND_TIMEOUT_S)
        if mode == "error":
            return result(1, b"", self.output([f"find: {root}: No such file or directory"]))
        if self.symlink and root == ROOTS[0]:
            return result(0, b"", b"")
        lines = [(root.encode("utf-8") + rel) if isinstance(rel, bytes) else root + rel
                 for rel in sorted(self.listing(), key=lambda r: r if isinstance(r, bytes) else r.encode("utf-8"))]
        if mode == "partial":
            return result(1, self.output(lines), self.output([f"find: {root}Private: Permission denied"]))
        return result(0, self.output(lines), b"")

    def stat(self, paths, argv):
        self.stat_calls += 1
        mode = self.stat_mode.get(self.stat_calls) if isinstance(self.stat_mode, dict) else self.stat_mode
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, main.DEVICE_STAT_TIMEOUT_S)
        if mode == "fail":
            return result(1, b"", self.output(["stat: device went away"]))
        files, out, err = self.listing(), [], []
        for path in paths:
            root = next(r for r in ROOTS if path.startswith(r))
            rel = path[len(root):]
            if rel in files:
                size, mtime = files[rel]
                out.append(f"{size} {mtime} {path}")
            else:
                err.append(f"stat: '{path}': No such file or directory")
        if mode == "silent":
            out = []
        return result(1 if err else 0, self.output(out), self.output(err))


def result(code, stdout, stderr):
    return types.SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class FakeAdb:
    """A fake `adb` over fake phones, installed as main.subprocess.run. Every call must pass assert_readonly
    (this module's allowlist), carry stdin=DEVNULL, capture its output and have a timeout; each is recorded in
    .calls as (argv, kwargs). A call that breaks a rule raises AssertionError AND is recorded in .violations —
    the helper's never-raise handlers could swallow the exception, the record they cannot (the fake_adb fixture
    fails the test on any). missing: no adb binary. devices: None | "fail" | "timeout" for `adb devices`.
    banner: `adb devices` prints the old daemon-start lines on stdout first."""

    def __init__(self, phones=None, missing=False, devices=None, banner=False):
        self.phones, self.missing, self.devices_mode, self.banner = dict(phones or {}), missing, devices, banner
        self.calls, self.violations = [], []

    @staticmethod
    def check(argv, args, kwargs):
        """The rules for ONE adb call -> its device words; AssertionError when one is broken."""
        assert not args, f"positional subprocess.run arguments: {args!r}"
        words = assert_readonly(argv)
        assert kwargs.get("stdin") is subprocess.DEVNULL, f"adb must never read the caller's stdin: {argv!r}"
        assert kwargs.get("capture_output") is True, f"adb output must be captured: {argv!r}"
        assert isinstance(kwargs.get("timeout"), (int, float)) and kwargs["timeout"] > 0, f"no timeout: {argv!r}"
        assert not kwargs.get("check") and not kwargs.get("shell"), f"check/shell set: {argv!r}"
        return words

    def run(self, argv, *args, **kwargs):
        argv = list(argv)
        self.calls.append((argv, kwargs))
        try:
            words = self.check(argv, args, kwargs)
        except AssertionError as e:
            self.violations.append(str(e))
            raise
        if self.missing:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        if words == ["devices"]:
            if self.devices_mode == "timeout":
                raise subprocess.TimeoutExpired(argv, main.DEVICE_ADB_TIMEOUT_S)
            if self.devices_mode == "fail":
                return result(1, b"", b"adb: failed to start daemon\r\nerror: cannot connect to daemon\r\n")
            lines = ["* daemon not running; starting now at tcp:5037", "* daemon started successfully"] \
                if self.banner else []
            lines += ["List of devices attached"] + [f"{serial}\t{phone.state}" for serial, phone in
                                                     self.phones.items() if phone.state is not None] + [""]
            return result(0, "".join(line + "\r\n" for line in lines).encode("utf-8"), b"")
        serial = argv[2]                                    # assert_readonly: a shell command carries -s
        phone = self.phones.get(serial)
        if phone is None or phone.state is None:
            return result(1, b"", f"adb: device '{serial}' not found\r\n".encode("utf-8"))
        if phone.state != "device":
            return result(1, b"", f"adb: device {phone.state}\r\n".encode("utf-8"))
        if words[0] == "find":
            return phone.find(words[1], argv)
        return phone.stat(words[3:], argv)

    def argvs(self):
        return [argv for argv, _kw in self.calls]

    def device_calls(self, serial, verb):
        return [argv for argv in self.argvs() if argv[1:3] == ["-s", serial] and argv[4:5] == [verb]]


@pytest.fixture()
def fake_adb(monkeypatch):
    """install(phones=..., **options) -> the FakeAdb now answering every subprocess.run (main's is the global
    subprocess module, so nothing can reach a real adb). At teardown the test fails if ANY call of any fake it
    installed broke a rule — even when the code under test caught the AssertionError."""
    installed = []

    def install(phones=None, **options):
        fake = FakeAdb(phones, **options)
        installed.append(fake)
        monkeypatch.setattr(main.subprocess, "run", fake.run)
        return fake

    yield install
    violations = [v for fake in installed for v in fake.violations]
    assert violations == [], f"adb calls broke the read-only rules: {violations}"


def listed(device):
    """{path: (size, mtime)} of one device_inventory record."""
    return {f["path"]: (f["size"], f["mtime"]) for f in device["files"]}


# ============================================================================ (a) the inventory

def test_inventory_lists_every_file_with_size_and_mtime_over_crlf_and_hostile_names(fake_adb):
    movies = {rel: (1000 + i, 1714778282 + i) for i, rel in enumerate(HOSTILE)}
    series = {"Series/Other/Season 01/Other.S01E01 [555555].mkv": (7, 1600000000)}
    fake = fake_adb({MOVIES: Phone(files=movies), SERIES: Phone(files=series, crlf=False)}, banner=True)
    inv = main.device_inventory(ALIASES)

    assert inv["complete"] is True and inv["unconfigured"] == []
    assert list(inv["devices"]) == ["movies", "series", "others"]           # aliases order
    mov = inv["devices"]["movies"]
    assert (mov["alias"], mov["serial"], mov["status"], mov["detail"], mov["root"], mov["stat_errors"]) == (
        "movies", MOVIES, "ok", None, "/sdcard/Media/", 0)
    assert listed(mov) == {"/sdcard/Media/" + rel: stat for rel, stat in movies.items()}
    assert [f["path"] for f in mov["files"]] == sorted(f["path"] for f in mov["files"])
    assert {f["name"] for f in mov["files"]} == {rel.rsplit("/", 1)[1] for rel in HOSTILE}
    assert "Two  spaces and a trailing one [333333].mkv " in {f["name"] for f in mov["files"]}  # never stripped
    assert listed(inv["devices"]["series"]) == {"/sdcard/Media/" + rel: stat for rel, stat in series.items()}
    assert inv["devices"]["others"] == {"alias": "others", "serial": "<NEW_PIXEL_SERIAL>", "status": "skipped",
                                        "detail": "placeholder", "root": None, "files": [], "stat_errors": 0}
    # one `adb devices`, then per phone one find (the first root lists) and its stat batch(es)
    verbs = [(argv[2] if argv[1:2] == ["-s"] else None, argv[4] if len(argv) > 4 else argv[1]) for argv in fake.argvs()]
    assert verbs[0] == (None, "devices") and verbs.count((None, "devices")) == 1
    assert verbs.count((MOVIES, "find")) == 1 and verbs.count((SERIES, "find")) == 1


def test_default_aliases_are_device_aliases_read_at_call_time(fake_adb, monkeypatch):
    monkeypatch.setattr(main, "DEVICE_ALIASES", {"series": SERIES, "others": "<NEW_PIXEL_SERIAL>"})
    fake_adb({SERIES: Phone(files={"Series/a [abcdef].mkv": (1, 2)})})
    inv = main.device_inventory()
    assert list(inv["devices"]) == ["series", "others"] and inv["complete"] is True


@pytest.mark.parametrize("placeholder", ["<NEW_PIXEL_SERIAL>", "<OTHERS PIXEL>", "", "   ", None])
def test_a_placeholder_serial_is_skipped_and_never_reaches_adb(fake_adb, placeholder):
    fake = fake_adb({})
    inv = main.device_inventory({"others": placeholder})
    assert fake.calls == []                                                  # not even `adb devices`
    assert inv["devices"]["others"]["status"] == "skipped" and inv["devices"]["others"]["detail"] == "placeholder"
    assert inv["complete"] is False                                          # no phone was listed at all
    fake = fake_adb({SERIES: Phone(files={"Series/a [abcdef].mkv": (1, 2)})})
    inv = main.device_inventory({"series": SERIES, "others": placeholder})
    assert inv["complete"] is True                                           # a placeholder cannot hold anything
    if isinstance(placeholder, str) and placeholder.strip():
        assert all(placeholder not in argv for argv in fake.argvs())


@pytest.mark.parametrize("state,status", [
    (None, "not-connected"), ("offline", "offline"), ("unauthorized", "unauthorized"),
    ("authorizing", "unauthorized"), ("no permissions (missing udev rules?)", "unavailable"),
    ("recovery", "unavailable"), ("bootloader", "unavailable")])
def test_unplugged_offline_unauthorized_and_other_states_are_reported_never_raised(fake_adb, state, status):
    fake = fake_adb({MOVIES: Phone(state=state, files={"Movies/x [abcdef].mkv": (1, 2)}),
                     SERIES: Phone(files={"Series/y [fedcba].mkv": (3, 4)})})
    inv = main.device_inventory(ALIASES)
    mov = inv["devices"]["movies"]
    assert mov["status"] == status and mov["files"] == [] and mov["root"] is None and mov["detail"]
    if state not in (None, "offline", "unauthorized", "authorizing"):
        assert state in mov["detail"]                                        # the raw adb state is shown
    assert fake.device_calls(MOVIES, "find") == [] and fake.device_calls(MOVIES, "stat") == []
    assert inv["devices"]["series"]["status"] == "ok"                        # the other phone is still listed
    assert inv["complete"] is False


def test_a_missing_adb_binary_is_reported_for_every_real_phone(fake_adb):
    fake_adb({MOVIES: Phone()}, missing=True)
    inv = main.device_inventory(ALIASES)
    assert [d["status"] for d in inv["devices"].values()] == ["adb-missing", "adb-missing", "skipped"]
    assert "adb could not be started" in inv["devices"]["movies"]["detail"]
    assert inv["complete"] is False and inv["unconfigured"] == []


@pytest.mark.parametrize("mode,fragment", [("fail", "failed to start daemon"), ("timeout", "TimeoutExpired")])
def test_adb_devices_failing_or_hanging_is_an_error_per_phone(fake_adb, mode, fragment):
    fake = fake_adb({MOVIES: Phone(), SERIES: Phone()}, devices=mode)
    inv = main.device_inventory(ALIASES)
    assert [d["status"] for d in inv["devices"].values()] == ["error", "error", "skipped"]
    assert fragment in inv["devices"]["series"]["detail"] and inv["complete"] is False
    assert len(fake.calls) == 1                                              # nothing else was tried


def test_the_fallback_root_when_the_sdcard_symlink_lists_nothing(fake_adb):
    files = {"Series/Show/Show.S01E01 [abcdef].mkv": (5, 6), "Series/Show/Show [abcdef].mvmeta.json": (7, 8)}
    fake = fake_adb({SERIES: Phone(files=files, symlink=True)})
    dev = main.device_inventory({"series": SERIES})["devices"]["series"]
    assert (dev["status"], dev["root"]) == ("ok", "/storage/emulated/0/Media/")
    assert listed(dev) == {"/storage/emulated/0/Media/" + rel: stat for rel, stat in files.items()}
    finds = [device_words(" ".join(argv[4:]))[1] for argv in fake.device_calls(SERIES, "find")]
    assert finds == list(ROOTS)
    for argv in fake.device_calls(SERIES, "stat"):                           # stat addresses what find listed
        assert all(p.startswith("/storage/emulated/0/Media/") for p in device_words(" ".join(argv[4:]))[3:])


@pytest.mark.parametrize("phone,status,root", [
    (dict(find={ROOTS[0]: "error", ROOTS[1]: "error"}), "error", None),      # e.g. storage locked, no Media
    (dict(symlink=True, find={ROOTS[1]: "error"}), "error", None),          # one clean-empty root is not enough
    (dict(find={ROOTS[0]: "timeout"}), "error", None),
    (dict(files={"Series/a [abcdef].mkv": (1, 2)}, find={ROOTS[0]: "error"}), "ok", ROOTS[1]),
], ids=["both-roots-fail", "empty-then-error", "find-timeout", "fallback-lists-after-an-error"])
def test_a_media_root_that_cannot_be_listed_is_an_error_never_an_empty_phone(fake_adb, phone, status, root):
    fake_adb({SERIES: Phone(**phone)})
    inv = main.device_inventory({"series": SERIES})
    dev = inv["devices"]["series"]
    assert (dev["status"], dev["root"]) == (status, root)
    assert inv["complete"] is (status == "ok")
    if status == "error":
        assert dev["files"] == [] and dev["detail"]
    else:
        assert listed(dev) == {ROOTS[1] + "Series/a [abcdef].mkv": (1, 2)}


def test_an_empty_media_folder_listed_cleanly_is_a_complete_empty_phone(fake_adb):
    fake = fake_adb({SERIES: Phone(files={})})
    inv = main.device_inventory({"series": SERIES})
    dev = inv["devices"]["series"]
    assert (dev["status"], dev["root"], dev["files"], dev["detail"]) == ("ok", ROOTS[0], [], None)
    assert inv["complete"] is True
    assert len(fake.device_calls(SERIES, "find")) == 2 and fake.device_calls(SERIES, "stat") == []


def test_find_errors_next_to_a_listing_make_the_phone_partial(fake_adb):
    fake_adb({SERIES: Phone(files={"Series/a [abcdef].mkv": (1, 2)}, find={ROOTS[0]: "partial"})})
    inv = main.device_inventory({"series": SERIES})
    dev = inv["devices"]["series"]
    assert (dev["status"], len(dev["files"])) == ("partial", 1) and "Permission denied" in dev["detail"]
    assert inv["complete"] is False                     # its files are evidence, its silence is not


def test_stat_runs_in_bounded_batches_covering_every_file_exactly_once(fake_adb):
    files = {f"Movies/A Rather Long Folder Name For Batching {i:03d}/A Rather Long File Name {i:03d} [ab{i:04d}].mkv":
             (i, 1700000000 + i) for i in range(120)}
    huge = "Movies/Huge/" + "x" * 3600 + " [ffffff].mkv"                     # longer than one batch on its own
    files[huge] = (1, 1)
    fake = fake_adb({MOVIES: Phone(files=files)})
    dev = main.device_inventory({"movies": MOVIES})["devices"]["movies"]
    assert listed(dev) == {"/sdcard/Media/" + rel: stat for rel, stat in files.items()}
    assert dev["stat_errors"] == 0
    batches = []
    for argv in fake.device_calls(MOVIES, "stat"):
        command = " ".join(argv[4:])                                         # what the device shell receives
        paths = device_words(command)[3:]
        assert len(command.encode("utf-8")) <= main.DEVICE_STAT_BATCH_BYTES or len(paths) == 1
        batches.append(paths)
    assert len(batches) >= 4                                                 # ~13.6 KB of paths, 3.5 KB a batch
    assert [b for b in batches if "/sdcard/Media/" + huge in b] == [["/sdcard/Media/" + huge]]
    statted = [p for b in batches for p in b]
    assert sorted(statted) == sorted(listed(dev)) and len(statted) == len(set(statted))
    assert statted == sorted(statted)                                        # listing order, one pass


@pytest.mark.parametrize("mode", ["fail", "timeout", "silent"])
def test_a_failing_stat_batch_only_loses_sizes(fake_adb, mode):
    files = {f"Series/S/E{i:03d} with a fairly long name to force batching [ab{i:04d}].mkv": (i, i) for i in range(80)}
    fake_adb({SERIES: Phone(files=files, stat={1: mode})})
    inv = main.device_inventory({"series": SERIES})
    dev = inv["devices"]["series"]
    assert dev["status"] == "ok" and inv["complete"] is True and len(dev["files"]) == 80
    lost = [f for f in dev["files"] if f["size"] is None]
    assert lost and dev["stat_errors"] == len(lost) < 80
    assert all(f["mtime"] is None for f in lost)
    assert all((f["size"], f["mtime"]) == files[f["path"][len(ROOTS[0]):]] for f in dev["files"] if f["size"] is not None)


def test_a_name_that_is_not_utf8_keeps_the_listing(fake_adb):
    files = {b"Series/S/Bad \xff\xfe name [abcdef].mkv": (9, 9), "Series/S/Good [fedcba].mkv": (1, 2)}
    fake_adb({SERIES: Phone(files=files)})
    dev = main.device_inventory({"series": SERIES})["devices"]["series"]
    assert dev["status"] == "ok" and dev["stat_errors"] == 1
    bad = next(f for f in dev["files"] if "Bad" in f["name"])
    assert "\ufffd" in bad["name"] and bad["size"] is None                  # decoded with U+FFFD; stat misses it
    assert next(f for f in dev["files"] if "Good" in f["name"])["size"] == 1


def test_attached_phones_missing_from_the_aliases_are_reported_not_listed(fake_adb):
    fake = fake_adb({SERIES: Phone(files={"Series/a [abcdef].mkv": (1, 2)}),
                     "HTFAKE0003": Phone(files={"Movies/b [abcdef].mkv": (1, 2)}),
                     "emulator-5554": Phone(state="offline")})
    inv = main.device_inventory({"series": SERIES, "others": "<NEW_PIXEL_SERIAL>"})
    assert inv["unconfigured"] == [{"serial": "HTFAKE0003", "state": "device"},
                                   {"serial": "emulator-5554", "state": "offline"}]
    assert fake.device_calls("HTFAKE0003", "find") == []


def test_an_invalid_serial_is_an_error_and_never_reaches_adb(fake_adb):
    fake = fake_adb({SERIES: Phone(files={})})
    inv = main.device_inventory({"bad": "FA75 ; rm -rf /", "series": SERIES})
    assert inv["devices"]["bad"]["status"] == "error" and "not a valid adb serial" in inv["devices"]["bad"]["detail"]
    assert inv["complete"] is False                                          # a configured phone was not listed
    assert all("FA75" not in " ".join(argv) for argv in fake.argvs())


def test_a_failure_inside_the_listing_is_an_error_record_never_an_exception(fake_adb, monkeypatch):
    fake_adb({SERIES: Phone(files={"Series/a [abcdef].mkv": (1, 2)})})
    monkeypatch.setattr(main, "_dev_batches", lambda paths: (_ for _ in ()).throw(RuntimeError("boom")))
    inv = main.device_inventory({"series": SERIES})
    dev = inv["devices"]["series"]
    assert (dev["status"], dev["files"]) == ("error", []) and "boom" in dev["detail"]
    assert inv["complete"] is False


# ============================================================================ (b) READ-ONLY, pinned

def test_every_adb_call_is_read_only_devnull_fed_bounded_and_captured(fake_adb):
    """The FakeAdb asserts all of this on every call of every test in this module; this test drives every
    branch at once and pins the complete vocabulary of what reaches a phone."""
    fake = fake_adb({MOVIES: Phone(files={rel: (1, 2) for rel in HOSTILE}, symlink=True, stat={1: "fail"}),
                     SERIES: Phone(files={"Series/x [abcdef].mkv": (1, 2)}, find={ROOTS[0]: "partial"}),
                     "HTFAKE0003": Phone(state="unauthorized")})
    main.device_inventory({**ALIASES, "third": "HTFAKE0003"})
    verbs = {tuple(assert_readonly(argv)[:1]) for argv in fake.argvs()}
    assert verbs == {("devices",), ("find",), ("stat",)}
    for argv, kwargs in fake.calls:
        assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["capture_output"] is True
        assert not ({"push", "pull", "install", "uninstall", "sync", "root", "remount", "reboot", "tcpip",
                     "kill-server", "disconnect"} & set(argv))
        words = assert_readonly(argv)
        assert not ({"rm", "mv", "cp", "touch", "mkdir", "am", "pm", "dd", "chmod", "chown", "ln", "setprop",
                     "-delete", "-exec", "-execdir", "-ok"} & set(words))


@pytest.mark.parametrize("argv", [
    ["adb", "devices"], ["adb", "-s", MOVIES, "devices"],
    ["adb", "-s", MOVIES, "shell", "find", "'/sdcard/Media/'", "-type", "f"],
    ["adb", "-s", "192.168.1.5:5555", "shell", "find", "'/storage/emulated/0/Media/'", "-type", "f"],
    ["adb", "-s", MOVIES, "shell", "stat", "-c", "'%s %Y %n'", "'/sdcard/Media/a b.mkv'"],
    ["adb", "-s", MOVIES, "shell", "stat", "-c", "'%s %Y %n'", "'/sdcard/Media/Sorcerer'\\''s [x].mkv'",
     "'/storage/emulated/0/Media/Quote \"$(id)\" `id` ; rm -rf / [y].mkv'"],
])
def test_main_guard_allows_exactly_the_listing_shapes(argv):
    assert main._dev_argv_readonly(argv) is True
    assert_readonly(argv)                                   # and this module's independent allowlist agrees


@pytest.mark.parametrize("argv", [
    ["adb", "-s", MOVIES, "push", "local.mkv", "/sdcard/Media/x.mkv"],
    ["adb", "-s", MOVIES, "pull", "/sdcard/Media/x.mkv", "x.mkv"],
    ["adb", "-s", MOVIES, "install", "x.apk"],
    ["adb", "-s", MOVIES, "shell", "rm", "'/sdcard/Media/x.mkv'"],
    ["adb", "-s", MOVIES, "shell", "mv", "'/sdcard/Media/a'", "'/sdcard/Media/b'"],
    ["adb", "-s", MOVIES, "shell", "touch", "'/sdcard/Media/x'"],
    ["adb", "-s", MOVIES, "shell", "mkdir", "-p", "'/sdcard/Media/x'"],
    ["adb", "-s", MOVIES, "shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE"],
    ["adb", "-s", MOVIES, "shell", "pm", "clear", "com.google.android.apps.photos"],
    ["adb", "-s", MOVIES, "shell", "find", "'/sdcard/Media/'", "-type", "f", "-delete"],
    ["adb", "-s", MOVIES, "shell", "find", "'/sdcard/Media/'", "-type", "f", "-exec", "rm", "{}", "+"],
    ["adb", "-s", MOVIES, "shell", "find", "/sdcard/Media/", "-type", "f"],            # unquoted root
    ["adb", "shell", "find", "'/sdcard/Media/'", "-type", "f"],                          # no -s: any phone
    ["adb", "shell", "stat", "-c", "'%s %Y %n'", "'/sdcard/Media/a'"],
    ["adb", "-s", MOVIES, "shell", "find", "'/sdcard/'", "-type", "f"],                # not a Media root
    ["adb", "-s", MOVIES, "shell", "find", "'/sdcard/Media/'", "-type", "f;", "rm", "x"],
    ["adb", "-s", MOVIES, "shell", "stat", "-c", "'%s %Y %n'", "/sdcard/Media/a b.mkv"],   # unquoted path
    ["adb", "-s", MOVIES, "shell", "stat", "-c", "'%s %Y %n'", "'/sdcard/Media/a'; rm -rf /'"],
    ["adb", "-s", MOVIES, "shell", "stat", "-c", "'%s %Y %n'", "'/sdcard/Media/a'", ";", "rm", "'/x'"],
    ["adb", "-s", MOVIES, "shell", "stat", "-c", "'%s %Y %n'", "'/data/local/tmp/x'"],  # outside the roots
    ["adb", "-s", MOVIES, "shell", "stat", "-c", "'%s %Y %n'", "'/sdcard/Media/'"],     # the root itself
    ["adb", "-s", MOVIES, "shell", "stat", "-c", "'%s %Y %n'", "'/sdcard/Media/a\nrm -rf /'"],
    ["adb", "-s", MOVIES, "shell", "stat", "-c", "'%s %Y %n'"],                         # no path
    ["adb", "-s", MOVIES, "shell", "stat", "-c", "'%s'", "'/sdcard/Media/a'"],          # another format
    ["adb", "-s", MOVIES, "shell", "stat", "'/sdcard/Media/a'"],
    ["adb", "-s", MOVIES, "shell", "cat", "'/sdcard/Media/a [x].mvmeta.json'"],
    ["adb", "-s", MOVIES, "shell", "getprop persist.sys.timezone; date +%z"],
    ["adb", "-s", MOVIES, "shell"], ["adb", "-s", MOVIES], ["adb"], [],
    ["adb", "-s", "FA75 ; rm", "devices"], ["adb", "-s"], ["adb", "-s", "", "devices"],
    ["adb.exe", "devices"], ["sh", "-c", "adb devices"], "adb devices", ("adb", 7),
    ["adb", "devices", "-l"], ["adb", "-d", "devices"], ["adb", "reboot"], ["adb", "root"],
])
def test_main_guard_refuses_every_other_shape(argv):
    assert main._dev_argv_readonly(argv) is False


def test_a_refused_argv_never_runs(monkeypatch):
    ran = []
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(main._DeviceCommandRefused):
        main._dev_run(["adb", "-s", MOVIES, "shell", "rm", "'/sdcard/Media/x.mkv'"], 5)
    assert ran == []


def test_list_roots_follow_remote_root():
    assert main.DEVICE_LIST_ROOTS == (main.REMOTE_ROOT + "/", "/storage/emulated/0/Media/") == ROOTS
    assert main.DEVICE_STAT_FORMAT == STAT_FORMAT


# ============================================================================ (c) the per-object verdict

MOVIE = "mov-en-2020-devmovie"           # whole leaf + extras: a whole item and a split item
SPLITMOVIE = "mov-en-2021-devsplit"      # split leaf: 3 chunks + a FLAC holder
OTHER = "oth-en-2024-devfinal-e01"       # split leaf: 2 chunks (their sizes known from a block / a capture)
SEASON = "tv-en-2015-devshow-s02"        # season_map with a whole extras item
EP_WHOLE = "tv-en-2015-devshow-s02e02"
EP_SPLIT = "tv-en-2015-devshow-s02e03"   # combined-episode file, split
EP_ALIAS = "tv-en-2015-devshow-s02e04"   # multi_ep_alias -> EP_SPLIT
ANIME = "ani-ja-2006-devanime07"
WHOLE_SIZE = 4_000_000_000               # a split entry's tech_spec sizes the WHOLE file — never a chunk


def _sha(tag):
    return hashlib.sha256(tag.encode()).hexdigest()


def _leaf(mid, folder, filename, size=None, chunks=0, holder=False):
    sid = mvcommon.generate_short_id(mid)
    stem, ext = os.path.splitext(filename)
    entry = {"short_id": sid, "filename": filename, "folder_path": str(folder), "status": "archived",
             "uploaded": True, "search_term": f"{stem} [{sid}]{ext}", "hash": _sha(mid)}
    if size is not None:
        entry["tech_spec"] = {"resolution": "1080p", "size_bytes": size}
    if chunks:
        entry["split_info"] = {"is_split": True, "method": "SIZE_GB", "val": "4", "total_chunks": chunks,
                               "chunks": [{"filename": f"{stem} [{sid}].chunk.{i:03d}.mkv",
                                           "hash": _sha(f"{mid}#{i}")} for i in range(1, chunks + 1)]}
        if holder:
            entry["split_info"]["carried_out_tracks"] = [{
                "track_id": 2, "codec": "A_FLAC", "language": "eng",
                "holder_filename": f"{stem} [{sid}].holder.mkv", "holder_hash": _sha(f"{mid}#holder")}]
    return entry


def _extra(title_id, group_rel, sub_rel, size=None, chunks=0):
    filename = sub_rel.split("/")[-1]
    sid = mvcommon.generate_short_id(f"{title_id}::{group_rel}/{sub_rel}")
    stem, ext = os.path.splitext(filename)
    item = {"filename": filename, "sub_rel": sub_rel, "short_id": sid, "hash": _sha(f"{title_id}::{sub_rel}"),
            "status": "archived", "uploaded": True, "search_term": f"{stem} [{sid}]{ext}"}
    if size is not None:
        item["tech_spec"] = {"size_bytes": size}
    if chunks:
        item["split_info"] = {"is_split": True, "method": "COUNT", "val": str(chunks), "total_chunks": chunks,
                              "chunks": [{"filename": f"{stem} [{sid}].chunk.{i:03d}.mkv", "hash": _sha(f"{sid}#{i}")}
                                         for i in range(1, chunks + 1)]}
    return item


def sidecar_of(filename, short_id):
    """The sidecar name write_remote_mvmeta gives an entry / extras item — its own rule, restated here."""
    return f"{os.path.splitext(filename)[0]} [{short_id}]{main.MVMETA_SUFFIX}"


def names(lib):
    """{(manual_id, label): uploaded name} of every uploaded object."""
    return {(o["manual_id"], o["label"]): o["uploaded_name"] for o in mvcommon.iter_uploaded_objects(lib)}


@pytest.fixture()
def world(tmp_path):
    """An in-memory library over all four prefixes (folders under tmp_path): chunks, a FLAC holder, extras whole
    + split, a season_map with extras, a multi_ep_alias; OTHER's chunk 1 has a trusted block with its size,
    chunk 2 a push capture with its size."""
    root = tmp_path / "Media"
    other_dir, season_dir = root / "Sports" / "Dev Final", root / "Series" / "Dev Show" / "Season 02"
    lib = {
        MOVIE: {**_leaf(MOVIE, root / "Movies" / "Dev Movie (2020)", "Dev.Movie.2020.mkv", size=2_000_000),
                "extras": {"groups": {"Specials": {"added_date": "2026-09-01", "items": [
                    _extra(MOVIE, "Specials", "BTS.mkv", size=5000),
                    _extra(MOVIE, "Specials", "Bonus/Making Of.mkv", size=9000, chunks=2)]}}}},
        SPLITMOVIE: _leaf(SPLITMOVIE, root / "Movies" / "Dev Split (2021)", "Dev.Split.2021.mkv", size=WHOLE_SIZE,
                          chunks=3, holder=True),
        OTHER: _leaf(OTHER, other_dir, "Dev.Final.2024.mkv", size=WHOLE_SIZE, chunks=2),
        SEASON: {"type": "season_map", "folder_path": str(season_dir), "total_episodes": 3,
                 "children": [EP_WHOLE, EP_SPLIT, EP_ALIAS],
                 "extras": {"groups": {"Specials": {"added_date": "2026-09-01", "items": [
                     _extra(SEASON, "Specials", "Recap.mkv")]}}}},
        EP_WHOLE: {**_leaf(EP_WHOLE, season_dir, "Dev.Show.S02E02.mkv", size=1_500_000), "parent_id": SEASON},
        EP_SPLIT: {**_leaf(EP_SPLIT, season_dir, "Dev.Show.S02E03E04.mkv", chunks=2), "parent_id": SEASON},
        EP_ALIAS: {"type": "multi_ep_alias", "alias_of": EP_SPLIT, "parent_id": SEASON},
        ANIME: _leaf(ANIME, root / "Anime" / "Dev Anime", "Dev Anime - 07 [1080p].mkv", size=700_000),
    }
    chunks = lib[OTHER]["split_info"]["chunks"]
    chunks[0]["gphotos"] = mvcommon.new_gphotos_block(chunks[0]["hash"], size_bytes=555)
    other_dir.mkdir(parents=True)
    capture = {"capture_version": 2, "manual_id": OTHER, "short_id": lib[OTHER]["short_id"], "pushes": [
        {"captured_at": "2026-09-20T10:00:00Z", "device_id": MOVIES, "objects": [
            {"role": "chunk", "index": 2, "sha256": chunks[1]["hash"], "uploaded_name": chunks[1]["filename"],
             "local_name": chunks[1]["filename"], "size_bytes": 777}]}]}
    (other_dir / f"{lib[OTHER]['short_id']}.gpcapture.json").write_text(json.dumps(capture), encoding="utf-8")
    return lib


def _scenario_phones(lib):
    """(movies phone files, series phone files) for the scenario whose verdicts are EXPECTED."""
    n = names(lib)
    bts, making = lib[MOVIE]["extras"]["groups"]["Specials"]["items"]
    movies_phone = {
        "Movies/Dev Movie (2020)/" + n[(MOVIE, "whole")]: (2_000_000, 1),                      # size agrees
        "Movies/Dev Movie (2020)/Specials/" + n[(MOVIE, "extra Specials/BTS.mkv")]: (4999, 2),  # other bytes
        "Movies/Dev Movie (2020)/Specials/" + sidecar_of("BTS.mkv", bts["short_id"]): (300, 3),
        "Movies/Dev Movie (2020)/Specials/Bonus/" + n[(MOVIE, "extra Specials/Bonus/Making Of.mkv chunk 1/2")]:
            (4500, 4),
        "Movies/Dev Movie (2020)/Specials/Bonus/" + sidecar_of("Making Of.mkv", making["short_id"]): (300, 5),
        "Movies/Dev Split (2021)/" + n[(SPLITMOVIE, "chunk 1/3")]: (123, 6),
        "Movies/Dev Split (2021)/" + sidecar_of(lib[SPLITMOVIE]["filename"], lib[SPLITMOVIE]["short_id"]): (300, 7),
        "Sports/Dev Final/" + n[(OTHER, "chunk 1/2")]: (555, 8),                               # block: agrees
        "Sports/Dev Final/" + n[(OTHER, "chunk 2/2")]: (778, 9),                               # capture: 777
    }
    series_phone = {
        "Series/Dev Show/Season 02/Specials/" + n[(SEASON, "extra Specials/Recap.mkv")]: (42, 10),
        "Anime/Dev Anime/" + sidecar_of(lib[ANIME]["filename"], lib[ANIME]["short_id"]): (300, 11),
        "Series/Dev Show/Season 02/" + n[(EP_SPLIT, "chunk 1/2")] + main.PARTIAL_SUFFIX: (10, 12),
    }
    return movies_phone, series_phone


EXPECTED = {
    (ANIME, "whole"): "freed",                                      # only its sidecar, on the series phone
    (MOVIE, "whole"): "on_device",
    (MOVIE, "extra Specials/BTS.mkv"): "freed",                     # a same-named file of OTHER bytes + sidecar
    (MOVIE, "extra Specials/Bonus/Making Of.mkv chunk 1/2"): "on_device",
    (MOVIE, "extra Specials/Bonus/Making Of.mkv chunk 2/2"): "freed",
    (SPLITMOVIE, "chunk 1/3"): "on_device",                         # tech_spec (whole-file size) never compared
    (SPLITMOVIE, "chunk 2/3"): "freed",
    (SPLITMOVIE, "chunk 3/3"): "freed",
    (SPLITMOVIE, "holder"): "freed",
    (OTHER, "chunk 1/2"): "on_device",
    (OTHER, "chunk 2/2"): "absent",                                 # other bytes, no sidecar
    (SEASON, "extra Specials/Recap.mkv"): "on_device",
    (EP_WHOLE, "whole"): "absent",
    (EP_SPLIT, "chunk 1/2"): "absent",                              # only a .partial leftover
    (EP_SPLIT, "chunk 2/2"): "absent",
}


def _classify(fake_adb, lib, movies=None, series=None, aliases=ALIASES, objects=None):
    movies_phone, series_phone = _scenario_phones(lib)
    fake_adb({MOVIES: movies or Phone(files=movies_phone), SERIES: series or Phone(files=series_phone)})
    return main.classify_device_objects(lib, main.device_inventory(aliases), objects=objects)


def _states(result):
    return {(o["manual_id"], o["label"]): o["state"] for o in result["objects"]}


def test_every_object_of_all_four_categories_gets_its_verdict(fake_adb, world):
    res = _classify(fake_adb, world)
    assert _states(res) == EXPECTED
    assert list(_states(res)) == [(o["manual_id"], o["label"]) for o in mvcommon.iter_uploaded_objects(world)]
    assert res["counts"] == {"on_device": 5, "freed": 6, "absent": 4, "unchecked": 0}
    assert (res["complete"], res["incomplete_devices"]) == (True, {})
    assert {o["category"] for o in res["objects"]} == {"movies", "tv", "anime", "others"}
    assert {o["kind"] for o in res["objects"]} == {"whole", "chunk", "holder", "extra_whole", "extra_chunk"}
    assert tuple(res["counts"]) == main.DEVICE_STATES == ("on_device", "freed", "absent", "unchecked")


def test_the_evidence_names_phone_path_size_and_sidecar(fake_adb, world):
    by = {(o["manual_id"], o["label"]): o for o in _classify(fake_adb, world)["objects"]}
    for o in by.values():
        assert set(o) == {"manual_id", "category", "kind", "index", "label", "sha256", "uploaded_name", "slot",
                          "state", "expected_size", "size_source", "copies", "sidecars", "partials"}
    whole = by[(MOVIE, "whole")]
    (found,) = whole["copies"]
    assert found == {"alias": "movies", "serial": MOVIES, "size": 2_000_000, "mtime": 1, "name_match": "exact",
                     "size_match": True, "path": "/sdcard/Media/Movies/Dev Movie (2020)/" + whole["uploaded_name"]}
    assert (whole["expected_size"], whole["size_source"], whole["sha256"]) == (2_000_000, "tech_spec",
                                                                              world[MOVIE]["hash"])
    bts = by[(MOVIE, "extra Specials/BTS.mkv")]
    assert [c["size_match"] for c in bts["copies"]] == [False] and len(bts["sidecars"]) == 1
    assert (bts["expected_size"], bts["size_source"]) == (5000, "tech_spec")
    chunk = by[(SPLITMOVIE, "chunk 1/3")]
    assert (chunk["expected_size"], chunk["size_source"], chunk["copies"][0]["size_match"]) == (None, None, None)
    holder = by[(SPLITMOVIE, "holder")]
    assert holder["sidecars"][0]["path"].endswith(
        "/" + sidecar_of(world[SPLITMOVIE]["filename"], world[SPLITMOVIE]["short_id"]))
    assert all(by[(SPLITMOVIE, f"chunk {i}/3")]["sidecars"] == holder["sidecars"] for i in (1, 2, 3))
    c1, c2 = by[(OTHER, "chunk 1/2")], by[(OTHER, "chunk 2/2")]
    assert (c1["expected_size"], c1["size_source"], c1["copies"][0]["size_match"]) == (555, "block", True)
    assert (c2["expected_size"], c2["size_source"], c2["copies"][0]["size_match"]) == (777, "capture", False)
    episode = by[(EP_SPLIT, "chunk 1/2")]
    assert episode["copies"] == [] and episode["sidecars"] == []
    assert [(p["alias"], p["path"].endswith(episode["uploaded_name"] + main.PARTIAL_SUFFIX))
            for p in episode["partials"]] == [("series", True)]
    anime = by[(ANIME, "whole")]
    assert [(s["alias"], s["mtime"], s["name_match"]) for s in anime["sidecars"]] == [("series", 11, "exact")]
    assert by[(OTHER, "chunk 1/2")]["slot"] == (OTHER, "split_info", "chunks", 0)


@pytest.mark.parametrize("state", [None, "offline", "unauthorized"], ids=["unplugged", "offline", "unauthorized"])
def test_an_incomplete_device_check_never_claims_absent(fake_adb, world, state):
    res = _classify(fake_adb, world, series=Phone(state=state, files=_scenario_phones(world)[1]))
    expected = {key: ("unchecked" if state == "absent" else state) for key, state in EXPECTED.items()}
    expected[(ANIME, "whole")] = "unchecked"                   # its sidecar is on the phone that was not listed
    expected[(SEASON, "extra Specials/Recap.mkv")] = "unchecked"
    assert _states(res) == expected
    assert res["complete"] is False and list(res["incomplete_devices"]) == ["series"]
    assert res["counts"]["absent"] == 0


def test_a_partially_listed_phone_keeps_its_evidence_but_blocks_absent(fake_adb, world):
    res = _classify(fake_adb, world, series=Phone(files=_scenario_phones(world)[1], find={ROOTS[0]: "partial"}))
    assert _states(res) == {key: ("unchecked" if state == "absent" else state) for key, state in EXPECTED.items()}
    assert res["incomplete_devices"] == {"series": "partial"}


def test_without_a_real_phone_nothing_can_be_absent(fake_adb, world):
    fake = fake_adb({})
    res = main.classify_device_objects(world, main.device_inventory({"others": "<NEW_PIXEL_SERIAL>"}))
    assert fake.calls == [] and res["complete"] is False and res["incomplete_devices"] == {}
    assert res["counts"] == {"on_device": 0, "freed": 0, "absent": 0, "unchecked": len(EXPECTED)}


def test_a_copy_on_any_phone_outranks_a_sidecar_on_another(fake_adb, world):
    anime = world[ANIME]
    fake_adb({MOVIES: Phone(files={"Anime/" + sidecar_of(anime["filename"], anime["short_id"]): (300, 1)}),
              SERIES: Phone(files={"Anime/" + names(world)[(ANIME, "whole")]: (700_000, 2)})})
    res = main.classify_device_objects(world, main.device_inventory(ALIASES),
                                       objects=list(mvcommon.iter_uploaded_objects(world, [ANIME])))
    (obj,) = res["objects"]
    assert obj["state"] == "on_device"
    assert [(c["alias"], c["size_match"]) for c in obj["copies"]] == [("series", True)]
    assert [s["alias"] for s in obj["sidecars"]] == ["movies"]


def test_a_normalized_name_match_counts_and_is_flagged_but_exact_wins(fake_adb, world):
    name = names(world)[(EP_WHOLE, "whole")]
    mangled = name.lower().replace(" [", "  [")                 # case + a doubled space (PLAN P-6)
    only = list(mvcommon.iter_uploaded_objects(world, [EP_WHOLE]))
    fake_adb({SERIES: Phone(files={"Series/" + mangled: (1_500_000, 1)})})
    (obj,) = main.classify_device_objects(world, main.device_inventory({"series": SERIES}), objects=only)["objects"]
    assert obj["state"] == "on_device"
    assert [(c["name_match"], c["size_match"]) for c in obj["copies"]] == [("normalized", True)]
    fake_adb({SERIES: Phone(files={"Series/" + mangled: (1, 1), "Series/b/" + name: (1_500_000, 2)})})
    (obj,) = main.classify_device_objects(world, main.device_inventory({"series": SERIES}), objects=only)["objects"]
    assert [(c["name_match"], c["path"]) for c in obj["copies"]] == [("exact", "/sdcard/Media/Series/b/" + name)]


def test_objects_without_an_uploaded_name_or_without_their_short_id_tag(world):
    ep = world[EP_WHOLE]
    untagged = ep["filename"]                                  # uploaded under the plain name (IMP-C27 (b))
    inventory = {"complete": True, "unconfigured": [], "devices": {"series": {
        "alias": "series", "serial": SERIES, "status": "ok", "detail": None, "root": ROOTS[0], "stat_errors": 0,
        "files": [{"path": ROOTS[0] + "Series/" + sidecar_of(ep["filename"], ep["short_id"]),
                   "name": sidecar_of(ep["filename"], ep["short_id"]), "size": 300, "mtime": 1}]}}}
    (obj,) = mvcommon.iter_uploaded_objects(world, [EP_WHOLE])
    res = main.classify_device_objects(world, inventory, objects=[dict(obj, uploaded_name=None),
                                                                  dict(obj, uploaded_name=untagged), obj])
    assert [o["state"] for o in res["objects"]] == ["unchecked", "absent", "freed"]
    assert res["objects"][1]["sidecars"] == []                 # an untagged name has no sidecar to find
    inventory["devices"]["series"]["files"].append(
        {"path": ROOTS[0] + "Series/" + untagged, "name": untagged, "size": 1_500_000, "mtime": 2})
    res = main.classify_device_objects(world, inventory, objects=[dict(obj, uploaded_name=untagged)])
    assert [(o["state"], o["copies"][0]["size_match"]) for o in res["objects"]] == [("on_device", True)]


@pytest.mark.parametrize("uploaded_name,short_id,expected", [
    ("Show.S01E02 [a1b2c3].mkv", "a1b2c3", "Show.S01E02 [a1b2c3].mvmeta.json"),
    ("Movie.2019 [d4e5f6].chunk.002.mkv", "d4e5f6", "Movie.2019 [d4e5f6].mvmeta.json"),
    ("Movie.2019 [d4e5f6].holder.mkv", "d4e5f6", "Movie.2019 [d4e5f6].mvmeta.json"),
    ("Clip.mkv [0a0b0c].mkv", "0a0b0c", "Clip.mkv [0a0b0c].mvmeta.json"),      # the real double extension
    ("NoExtension [0a0b0c]", "0a0b0c", "NoExtension [0a0b0c].mvmeta.json"),
    ("X [abc123] [abc123].mkv", "abc123", "X [abc123] [abc123].mvmeta.json"),  # the LAST tag is the push's
    ("Death Note - 07 [1080p] [abc123].mkv", "abc123", "Death Note - 07 [1080p] [abc123].mvmeta.json"),
    ("Show.S01E02.mkv", "a1b2c3", None),                                       # untagged
    ("Show [ffffff].mkv", "a1b2c3", None),                                     # another object's tag
    (None, "a1b2c3", None), ("x [a1b2c3].mkv", None, None), ("x [a1b2c3].mkv", "", None)])
def test_sidecar_name_rule(uploaded_name, short_id, expected):
    assert main._dev_sidecar_name(uploaded_name, short_id) == expected


def _pushed_sidecar(monkeypatch, entry, remote_dir="/sdcard/Media/X"):
    """The sidecar name the REAL write_remote_mvmeta pushes for `entry` (adb recorded, nothing run)."""
    pushed = []
    monkeypatch.setattr(main.subprocess, "run",
                        lambda argv, **kw: pushed.append(list(argv)) or types.SimpleNamespace(returncode=0))
    assert main.write_remote_mvmeta(["adb", "-s", MOVIES], remote_dir, "label", entry) is True
    (argv,) = pushed
    return argv[-1].rsplit("/", 1)[1]


@pytest.mark.parametrize("mid", [MOVIE, SPLITMOVIE, OTHER, EP_WHOLE, ANIME])
def test_drift_pin_sidecar_rule_equals_write_remote_mvmeta_for_leaves(world, monkeypatch, mid):
    sidecar = _pushed_sidecar(monkeypatch, world[mid])
    leaf = [o for o in mvcommon.iter_entry_objects(mid, world[mid]) if o["kind"] in ("whole", "chunk", "holder")]
    assert leaf and {main._dev_sidecar_name(o["uploaded_name"], o["short_id"]) for o in leaf} == {sidecar}


def test_drift_pin_sidecar_rule_equals_write_remote_mvmeta_for_extras(world, monkeypatch):
    for title in (MOVIE, SEASON):
        for item in world[title]["extras"]["groups"]["Specials"]["items"]:
            sidecar = _pushed_sidecar(monkeypatch, dict(item, folder_path="x"))   # push_one_extra's meta_entry
            objs = [o for o in mvcommon.iter_entry_objects(title, world[title]) if o["short_id"] == item["short_id"]]
            assert objs and {main._dev_sidecar_name(o["uploaded_name"], o["short_id"]) for o in objs} == {sidecar}


def test_the_objects_argument_and_alias_safety(fake_adb, world):
    movies_phone, series_phone = _scenario_phones(world)
    fake_adb({MOVIES: Phone(files=movies_phone), SERIES: Phone(files=series_phone)})
    inv = main.device_inventory(ALIASES)
    via_alias = main.classify_device_objects(world, inv, objects=list(mvcommon.iter_uploaded_objects(world, [EP_ALIAS])))
    assert [(o["manual_id"], o["label"], o["state"]) for o in via_alias["objects"]] == [
        (EP_SPLIT, "chunk 1/2", "absent"), (EP_SPLIT, "chunk 2/2", "absent")]
    assert via_alias["counts"] == {"on_device": 0, "freed": 0, "absent": 2, "unchecked": 0}
    everything = main.classify_device_objects(world, inv)
    assert EP_ALIAS not in {o["manual_id"] for o in everything["objects"]}       # the alias is its primary, once
    assert [o["kind"] for o in everything["objects"] if o["manual_id"] == SEASON] == ["extra_whole"]
    movie = main.classify_device_objects(world, inv, objects=list(mvcommon.iter_entry_objects(MOVIE, world[MOVIE])))
    assert [o["state"] for o in movie["objects"]] == ["on_device", "freed", "on_device", "freed"]
    assert main.classify_device_objects(world, inv, objects=[])["objects"] == []


def test_classification_is_pure(fake_adb, world, monkeypatch):
    movies_phone, series_phone = _scenario_phones(world)
    fake_adb({MOVIES: Phone(files=movies_phone), SERIES: Phone(files=series_phone)})
    inv = main.device_inventory(ALIASES)
    before_lib, before_inv = copy.deepcopy(world), copy.deepcopy(inv)
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: pytest.fail("classification must not run adb"))
    res = main.classify_device_objects(world, inv)
    assert world == before_lib and inv == before_inv
    for o in res["objects"]:                                   # the records are copies, never the inventory's
        for evidence in o["copies"] + o["sidecars"] + o["partials"]:
            evidence["path"] = "changed"
    assert inv == before_inv


# ============================================================================ (d) round trip through the push code

def _seed_parts(folder, names, payload):
    """Pre-seed a _parts dir so cmd_push / push_one_extra take their RESUME branch (no mkvmerge, no ffmpeg);
    every file gets its own size. Returns {name: sha256}."""
    folder.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for i, name in enumerate(names, 1):
        data = payload * (100 * i)
        (folder / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


def _split_leaf(mid, folder, filename, n_chunks, holder=False):
    """A local_ready split leaf whose chunks (+ a FLAC holder) wait in <folder>/_parts: cmd_push resumes them."""
    sid = mvcommon.generate_short_id(mid)
    stem = os.path.splitext(filename)[0]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(b"MASTER-" + mid.encode() * 100)
    chunk_names = [f"{stem} [{sid}].chunk.{i:03d}.mkv" for i in range(1, n_chunks + 1)]
    holder_name = f"{stem} [{sid}].holder.mkv"
    hashes = _seed_parts(folder / main.SPLIT_DIR_NAME, chunk_names + [holder_name] * holder, mid.encode())
    split = {"is_split": True, "method": "SIZE_GB", "val": "4", "total_chunks": n_chunks,
             "chunks": [{"filename": n, "hash": hashes[n]} for n in chunk_names]}
    if holder:
        split["carried_out_tracks"] = [{"track_id": 2, "codec": "A_FLAC", "language": "eng",
                                        "holder_filename": holder_name, "holder_hash": hashes[holder_name]}]
    return {"short_id": sid, "filename": filename, "folder_path": str(folder), "status": "local_ready",
            "uploaded": False, "search_term": f"{stem} [{sid}].mkv", "hash": _sha(mid), "split_info": split}


def _tree(root):
    """{relative path: (size, mtime_ns, sha256)} of every file under `root`."""
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob("*") if p.is_file()}


def test_round_trip_through_the_real_push_code_on_the_mock_device(sandbox_extras, mock_device, make_video,
                                                                  stub_tech_specs, fake_adb):
    """Push objects of all four categories with the REAL cmd_push / push_title_extras onto the mock device, then
    list it read-only: every object on_device (sizes agree with the captures); after "Free up space" (media
    gone, JSON sidecars kept) every object freed — so the sidecar rule matches what write_remote_mvmeta really
    pushed; with the sidecars gone too, absent. The listing never changes the phone."""
    media_root = sandbox_extras["sandbox"]["local_root"]
    title_id, (bts, trailer) = sandbox_extras["title_id"], sandbox_extras["items"]
    tv_id, oth_id, ani_id = "tv-en-2015-rtshow-s01e01", "oth-en-2024-rtmatch-e01", "ani-ja-2006-rtanime01"
    trailer_chunks = [f"Trailer [{trailer['short_id']}].chunk.{i:03d}.mkv" for i in (1, 2)]
    trailer_hashes = _seed_parts(sandbox_extras["extras_dir"] / main.SPLIT_DIR_NAME / trailer["short_id"],
                                 trailer_chunks, b"EXTRA-CHUNK")
    library = mvcommon.load_library()
    library[title_id]["extras"]["groups"]["Specials"]["items"][1]["split_info"] = {
        "is_split": True, "method": "COUNT", "val": "2", "total_chunks": 2,
        "chunks": [{"filename": n, "hash": trailer_hashes[n]} for n in trailer_chunks]}
    library[tv_id] = _split_leaf(tv_id, media_root / "Series" / "RT Show" / "Season 01", "RT.Show.S01E01.mkv", 2,
                                 holder=True)
    library[oth_id] = _split_leaf(oth_id, media_root / "Sports" / "RT Match", "RT.Match.2024.mkv", 3)
    mvcommon.save_library(library)
    anime_dir = media_root / "Anime" / "RT Anime"
    anime_dir.mkdir(parents=True)
    anime_path, _ = make_video(anime_dir / "RT Anime - 01 [1080p].mkv", marker=b"RT-ANIME\n")
    assert main.cmd_prep(ani_id, anime_path) is True
    for mid in (title_id, tv_id, oth_id, ani_id):
        assert main.cmd_push(mid) is True
    assert main.push_title_extras(mvcommon.load_library(), title_id, ("NONE", None)) is True

    library = mvcommon.load_library()
    objects = list(mvcommon.iter_uploaded_objects(library))
    assert {o["category"] for o in objects} == {"movies", "tv", "anime", "others"}
    assert sorted(o["kind"] for o in objects) == sorted(
        ["whole", "extra_whole", "extra_chunk", "extra_chunk", "chunk", "chunk", "holder",
         "chunk", "chunk", "chunk", "whole"])
    phone = mock_device / "sdcard" / "Media"
    fake = fake_adb({MOVIES: Phone(tree=str(phone))})
    before = _tree(phone)

    def verdicts():
        res = main.classify_device_objects(library, main.device_inventory({"movies": MOVIES}))
        assert res["complete"] is True and len(res["objects"]) == len(objects)
        return res

    res = verdicts()
    assert {o["state"] for o in res["objects"]} == {"on_device"}
    for o in res["objects"]:
        assert o["size_source"] == "capture" and o["expected_size"] > 0
        assert [(c["name_match"], c["size_match"]) for c in o["copies"]] == [("exact", True)]
        assert len(o["sidecars"]) == 1 and o["partials"] == []
    assert _tree(phone) == before                                 # listing the phone changed nothing on it

    for p in [p for p in phone.rglob("*") if p.is_file() and not p.name.endswith(main.MVMETA_SUFFIX)]:
        p.unlink()                                                # "Free up space": the media goes
    res = verdicts()
    assert {o["state"] for o in res["objects"]} == {"freed"}
    assert {s["name_match"] for o in res["objects"] for s in o["sidecars"]} == {"exact"}
    assert len({s["path"] for o in res["objects"] for s in o["sidecars"]}) == 6   # 4 entries + 2 extras items

    for p in list(phone.rglob("*" + main.MVMETA_SUFFIX)):
        p.unlink()
    assert {o["state"] for o in verdicts()["objects"]} == {"absent"}
    assert {assert_readonly(argv)[0] for argv in fake.argvs()} == {"devices", "find", "stat"}
