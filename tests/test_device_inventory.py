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
import os
import re
import subprocess
import types

import pytest

import main

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
    .calls as (argv, kwargs). missing: no adb binary. devices: None | "fail" | "timeout" for `adb devices`.
    banner: `adb devices` prints the old daemon-start lines on stdout first."""

    def __init__(self, phones=None, missing=False, devices=None, banner=False):
        self.phones, self.missing, self.devices_mode, self.banner = dict(phones or {}), missing, devices, banner
        self.calls = []

    def run(self, argv, *args, **kwargs):
        argv = list(argv)
        self.calls.append((argv, kwargs))
        assert not args, f"positional subprocess.run arguments: {args!r}"
        words = assert_readonly(argv)
        assert kwargs.get("stdin") is subprocess.DEVNULL, f"adb must never read the caller's stdin: {argv!r}"
        assert kwargs.get("capture_output") is True, f"adb output must be captured: {argv!r}"
        assert isinstance(kwargs.get("timeout"), (int, float)) and kwargs["timeout"] > 0, f"no timeout: {argv!r}"
        assert not kwargs.get("check") and not kwargs.get("shell"), f"check/shell set: {argv!r}"
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
    subprocess module, so nothing can reach a real adb)."""
    def install(phones=None, **options):
        fake = FakeAdb(phones, **options)
        monkeypatch.setattr(main.subprocess, "run", fake.run)
        return fake
    return install


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
