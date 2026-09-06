"""argv -> kwargs contract for the IMP-D22 CLI dispatcher blocks.

WHY THIS FILE EXISTS (the gap it closes)
----------------------------------------
Every other IMP-D22 test calls `cmd_prep_push_rep_enrich(...)` /
`cmd_prep_push_rep_season_enrich(...)` DIRECTLY with Python kwargs, so argv
parsing was entirely unpinned. The parsing for both new commands lives INLINE
in main.py's `if __name__ == "__main__":` elif-chain (there is no extracted
helper for it, unlike the functions `tests/test_cli_parsers.py` covers), so the
only way to test it is to drive that block. Nothing here pins behaviour of the
COMMANDS themselves — only the exact kwargs a given argv produces.

MECHANISM
---------
`_compile_main_guard()` parses main.py with `ast`, locates the
`if __name__ == "__main__":` node, and compiles ONLY its body. `run_dispatcher`
execs that code object in a FRESH copy of `main.__dict__` where:

  * `sys` is a proxy carrying the test's argv (the real `sys.argv` is never
    mutated, so pytest's own argv is untouched), and
  * every module-level `cmd_*` callable is swapped for a recorder that captures
    `(args, kwargs)` and does nothing else.

`resolve_device` and `parse_extras_tokens` are deliberately left REAL — they are
part of the parsing contract under test ("assert what actually arrives").

`parse_argv` then normalises the recorded call through the REAL function
signature (`inspect.signature(...).bind(...).apply_defaults()`), so positional
arguments (the dispatcher passes `split_method` / `split_val` — and, for the
season command, `episode_range` — positionally) and keyword arguments compare
uniformly. Every assertion below is a WHOLE-DICT equality against
`expected_kwargs(...)`, so a stray change to ANY parameter fails loudly instead
of slipping through an under-specified assertion.

HERMETIC BY CONSTRUCTION
------------------------
No fixture, no tmp_path, no library, no network, no ADB, no disk. The autouse
`_no_io_allowed` fixture below hard-fails the test if the dispatcher block ever
reaches `load_library` / `save_library` / `subprocess.run` / `subprocess.Popen`
/ `requests.get` / `input()`, so "the real C:\\Media and the real library_*.json
are never touched" is STRUCTURAL here, not merely argued. Path strings use a
fictional `Z:\\` root and are never opened by anything.
"""
import ast
import inspect
import sys

import pytest

import main
import mvcommon


# ---------------------------------------------------------------------------
#   Harness
# ---------------------------------------------------------------------------

def _compile_main_guard():
    """Compile ONLY the body of main.py's `if __name__ == "__main__":` block.

    Parsing the source with `ast` (rather than importing main as `__main__`,
    which would re-execute the whole module) keeps the compiled code's line
    numbers pointing at the real main.py, so a failure inside the dispatcher
    reports the actual source line."""
    with open(main.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=main.__file__)

    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name) and test.left.id == "__name__"
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == "__main__"):
            return compile(ast.Module(body=node.body, type_ignores=[]),
                           main.__file__, "exec")

    raise AssertionError("main.py has no `if __name__ == \"__main__\":` block")


_MAIN_GUARD_CODE = _compile_main_guard()


class _SysProxy:
    """Stand-in for the `sys` module carrying a per-call `argv`.

    Everything except `argv` delegates to the REAL `sys` (so `sys.exit` still
    raises SystemExit and `sys.stdin.isatty()` still works). Using a proxy
    instead of monkeypatching `sys.argv` means the running pytest process's own
    argv is never mutated, not even transiently."""

    def __init__(self, argv):
        self.argv = list(argv)

    def __getattr__(self, name):
        return getattr(sys, name)


class _Recorder:
    """Replacement for a `cmd_*` function: captures the call, does nothing."""

    def __init__(self, name):
        self.name = name
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return True  # the dispatcher ignores the return value; be truthy anyway


def run_dispatcher(argv):
    """Execute main.py's dispatcher with `argv`; return [(cmd_name, recorder)].

    `argv` includes argv[0] (the program name), exactly as a shell provides it.
    Returns one entry per `cmd_*` that was actually called (normally exactly
    one). SystemExit from a usage guard propagates to the caller."""
    ns = dict(main.__dict__)
    ns["sys"] = _SysProxy(argv)

    recorders = {}
    for name, obj in list(main.__dict__.items()):
        if name.startswith("cmd_") and callable(obj):
            rec = _Recorder(name)
            recorders[name] = rec
            ns[name] = rec

    exec(_MAIN_GUARD_CODE, ns)
    return [(n, r) for n, r in recorders.items() if r.calls]


def parse_argv(argv):
    """`argv` -> (cmd_name, {parameter: value}) for the single command it fires.

    The kwargs dict is the FULL parameter set of the real function, produced by
    binding the recorded call against `inspect.signature(...)` and applying
    defaults — so a value the dispatcher passes positionally (split_method /
    split_val / episode_range) is reported under its real parameter name."""
    fired = run_dispatcher(argv)
    assert len(fired) == 1, f"expected exactly one cmd_* call, got {[n for n, _ in fired]}"
    name, rec = fired[0]
    assert len(rec.calls) == 1, f"{name} was called {len(rec.calls)} times"

    args, kwargs = rec.calls[0]
    bound = inspect.signature(getattr(main, name)).bind(*args, **kwargs)
    bound.apply_defaults()
    return name, dict(bound.arguments)


@pytest.fixture(autouse=True)
def _no_io_allowed(monkeypatch):
    """Hard guard: the dispatcher block must reach NO I/O of any kind.

    Parsing argv should never load or save a library, shell out to adb, spawn
    mainfetch, hit the network, or prompt. Any of those would mean the block is
    doing more than parsing — and, for the library/media calls, that a test
    could touch the REAL library_*.json / C:\\Media. Both the mvcommon and the
    main bindings of load_library/save_library are blocked (the IMP-A1
    import-by-value hazard: `main` holds its own binding, so patching only
    mvcommon would leave a live path)."""
    def _boom(what):
        def _raise(*a, **k):
            raise AssertionError(f"the argv dispatcher must never call {what}()")
        return _raise

    for mod, attr in [(mvcommon, "load_library"), (mvcommon, "save_library"),
                      (main, "load_library"), (main, "save_library")]:
        monkeypatch.setattr(mod, attr, _boom(f"{mod.__name__}.{attr}"))
    monkeypatch.setattr(main.subprocess, "run", _boom("subprocess.run"))
    monkeypatch.setattr(main.subprocess, "Popen", _boom("subprocess.Popen"))
    monkeypatch.setattr(main.requests, "get", _boom("requests.get"))
    monkeypatch.setattr("builtins.input", _boom("input"))


# ---------------------------------------------------------------------------
#   Per-command argv scaffolding + the canonical expected-kwargs builder
# ---------------------------------------------------------------------------

MOVIE = "prep_push_rep_enrich"
SEASON = "prep_push_rep_season_enrich"
LEGACY_MOVIE = "prep_push_rep"
LEGACY_SEASON = "prep_push_rep_season"

BOTH_NEW = [MOVIE, SEASON]

# Fictional root — these strings are argv payloads only; nothing ever opens
# them. Deliberately NOT under C:\Media.
FAKE_ROOT = r"Z:\MediaVaultTestSandbox"

_HEAD = {
    MOVIE:        ["mov-en-2016-janedoe", rf"{FAKE_ROOT}\Movies\Jane.mkv"],
    SEASON:       ["tv-en-2017-dark-s01", rf"{FAKE_ROOT}\Series\Dark\Season01"],
    LEGACY_MOVIE: ["mov-en-2016-janedoe", rf"{FAKE_ROOT}\Movies\Jane.mkv"],
    LEGACY_SEASON: ["tv-en-2017-dark-s01", rf"{FAKE_ROOT}\Series\Dark\Season01"],
}
_ID_KEY = {MOVIE: "manual_id", SEASON: "base_id",
           LEGACY_MOVIE: "manual_id", LEGACY_SEASON: "base_id"}
_PATH_KEY = {MOVIE: "filepath", SEASON: "folder_path",
             LEGACY_MOVIE: "filepath", LEGACY_SEASON: "folder_path"}
_HAS_EPISODES = {MOVIE: False, SEASON: True,
                 LEGACY_MOVIE: False, LEGACY_SEASON: True}
_IS_ENRICH = {MOVIE: True, SEASON: True,
              LEGACY_MOVIE: False, LEGACY_SEASON: False}


def argv_for(cmd, *extra, head=None):
    """`["main.py", <cmd>, <id>, <path>, *extra]` — a shell-style argv list."""
    return ["main.py", cmd, *(head if head is not None else _HEAD[cmd]), *extra]


def expected_kwargs(cmd, **overrides):
    """The FULL kwargs dict a minimal `cmd` invocation must produce, with any
    documented default replaced by `overrides`.

    Every test asserts whole-dict equality against this, so an accidental change
    to a parameter the test is not "about" still fails."""
    exp = {
        _ID_KEY[cmd]: _HEAD[cmd][0],
        _PATH_KEY[cmd]: _HEAD[cmd][1],
        "split_method": None,
        "split_val": None,
        "device_id": None,
        "eager_rehash": False,
        "temp_dir": None,
        "extras": None,
        "extras_size": None,
    }
    if _HAS_EPISODES[cmd]:
        exp["episode_range"] = None
    if _IS_ENRICH[cmd]:
        exp.update({"tmdb_id": None, "tvdb_id": None, "write_nfo": False,
                    "no_web": False, "no_nfo": False, "rename_choice": "ask"})
    exp.update(overrides)
    return exp


_CMD_FUNC = {MOVIE: "cmd_prep_push_rep_enrich",
             SEASON: "cmd_prep_push_rep_season_enrich",
             LEGACY_MOVIE: "cmd_prep_push_rep",
             LEGACY_SEASON: "cmd_prep_push_rep_season"}


def assert_argv(cmd, extra, **overrides):
    """Drive `cmd` with `extra` tokens and assert the FULL resulting kwargs."""
    name, kw = parse_argv(argv_for(cmd, *extra))
    assert name == _CMD_FUNC[cmd]
    assert kw == expected_kwargs(cmd, **overrides)
    return kw


# ===========================================================================
#   GAP 1a — the documented defaults (minimal invocation)
# ===========================================================================

@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_minimal_argv_yields_every_documented_default(cmd):
    """id + path ONLY -> every optional parameter at its documented default:
    no split, no device, no rehash, no tempdir, no extras, no tmdb/tvdb id,
    NFO off, web fallback on, rename gate at "ask" (Decisions 1/3/4)."""
    assert_argv(cmd, [])


def test_minimal_argv_ids_and_paths_land_in_the_right_slots():
    """The two positional slots are NOT interchangeable: the id is argv[2] and
    the path is everything after it, per command."""
    _, movie = parse_argv(argv_for(MOVIE))
    assert movie["manual_id"] == "mov-en-2016-janedoe"
    assert movie["filepath"] == rf"{FAKE_ROOT}\Movies\Jane.mkv"

    _, season = parse_argv(argv_for(SEASON))
    assert season["base_id"] == "tv-en-2017-dark-s01"
    assert season["folder_path"] == rf"{FAKE_ROOT}\Series\Dark\Season01"


# ===========================================================================
#   GAP 1b — split size methods
# ===========================================================================

@pytest.mark.parametrize("cmd", BOTH_NEW)
@pytest.mark.parametrize("method,val", [("SIZE_GB", "8"), ("SIZE_MB", "700"), ("COUNT", "4")])
def test_size_methods_land_in_split_method_and_split_val(cmd, method, val):
    """`SIZE_GB 8` / `SIZE_MB 700` / `COUNT 4` -> (split_method, split_val),
    both forwarded POSITIONALLY by the dispatcher and both kept as STRINGS."""
    kw = assert_argv(cmd, [method, val], split_method=method, split_val=val)
    assert isinstance(kw["split_val"], str), "the value is never coerced to int"


# ===========================================================================
#   GAP 1c — `device <name>` passes through resolve_device()
# ===========================================================================

@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_device_known_alias_is_resolved_to_a_serial(cmd):
    """`device movies` does NOT arrive as "movies" — the dispatcher calls
    `resolve_device(...)`, so the configured DEVICE_ALIASES serial arrives."""
    resolved = main.DEVICE_ALIASES["movies"]
    kw = assert_argv(cmd, ["device", "movies"], device_id=resolved)
    assert kw["device_id"] != "movies", "the alias must be resolved, not forwarded raw"


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_device_unknown_name_passes_through_unchanged(cmd):
    """An unknown alias (i.e. a raw ADB serial) passes through verbatim —
    `resolve_device`'s documented `DEVICE_ALIASES.get(arg, arg)` fallback."""
    assert "FA0000TESTSERIAL" not in main.DEVICE_ALIASES
    assert_argv(cmd, ["device", "FA0000TESTSERIAL"], device_id="FA0000TESTSERIAL")


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_device_alias_map_is_consulted_at_parse_time(cmd, monkeypatch):
    """Proof the alias table is read when the token is parsed (not baked in):
    a patched DEVICE_ALIASES changes what arrives."""
    monkeypatch.setattr(main, "DEVICE_ALIASES", {"testphone": "SERIAL-XYZ"})
    assert_argv(cmd, ["device", "testphone"], device_id="SERIAL-XYZ")


# ===========================================================================
#   GAP 1d — rehash / tempdir
# ===========================================================================

@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_rehash_is_a_bare_flag_that_sets_eager_rehash(cmd):
    assert_argv(cmd, ["rehash"], eager_rehash=True)


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_tempdir_takes_the_next_token_as_temp_dir(cmd):
    tdir = rf"{FAKE_ROOT}\scratch"
    assert_argv(cmd, ["tempdir", tdir], temp_dir=tdir)


# ===========================================================================
#   GAP 1e — --extras / --extras-size (IMP-D19/D20 parity)
# ===========================================================================

@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_extras_semicolon_list_splits_into_an_ordered_list(cmd):
    assert_argv(cmd, ["--extras", "Specials;Trailers"], extras=["Specials", "Trailers"])


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_extras_short_flag_and_repetition_accumulate_in_order(cmd):
    """`-extras` is the accepted short spelling and the flag is repeatable;
    every path lands in ONE flat, order-preserving list."""
    assert_argv(cmd, ["-extras", "A;B", "--extras", "C"], extras=["A", "B", "C"])


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_extras_size_compact_unit_form(cmd):
    assert_argv(cmd, ["--extras-size", "500mb"], extras_size=("SIZE_MB", "500"))


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_extras_size_three_token_form_consumes_both_value_tokens(cmd):
    """`--extras-size SIZE_MB 500` — the triplet form. The dispatcher's own
    look-ahead must hand parse_extras_tokens THREE tokens, otherwise the "500"
    would leak into the path."""
    assert_argv(cmd, ["--extras-size", "SIZE_MB", "500"], extras_size=("SIZE_MB", "500"))


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_extras_size_none_disables_extras_splitting(cmd):
    assert_argv(cmd, ["--extras-size", "none"], extras_size=("NONE", None))


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_extras_and_extras_size_together(cmd):
    assert_argv(cmd, ["--extras", "Specials", "--extras-size", "SIZE_GB", "8"],
                extras=["Specials"], extras_size=("SIZE_GB", "8"))


# ===========================================================================
#   GAP 1f — -tmdbid / -tvdbid (Decision 1: parsed here, refused in the command)
# ===========================================================================

@pytest.mark.parametrize("cmd", BOTH_NEW)
@pytest.mark.parametrize("spelling", ["-tmdbid", "--tmdbid"])
def test_tmdbid_both_spellings_forward_the_raw_string(cmd, spelling):
    """BOTH spellings are accepted and the value arrives as a STRING (the
    command/`cmd_set_tmdb` coerces all-digit strings to int later — the parser
    itself never converts)."""
    kw = assert_argv(cmd, [spelling, "550"], tmdb_id="550")
    assert kw["tmdb_id"] == "550" and isinstance(kw["tmdb_id"], str)


@pytest.mark.parametrize("cmd", BOTH_NEW)
@pytest.mark.parametrize("spelling", ["-tvdbid", "--tvdbid"])
def test_tvdbid_both_spellings_are_forwarded_not_refused_by_the_parser(cmd, spelling):
    """Decision 1 puts the REFUSAL inside the command (`_refuse_tvdbid`), not in
    the parser. So the dispatcher must FORWARD the value — this pins the
    forwarding, not the refusal."""
    kw = assert_argv(cmd, [spelling, "12345"], tvdb_id="12345")
    assert kw["tvdb_id"] == "12345" and isinstance(kw["tvdb_id"], str)


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_tmdbid_and_tvdbid_can_both_be_supplied(cmd):
    """Supplying both is legal at the parser level; the command decides."""
    assert_argv(cmd, ["-tmdbid", "550", "-tvdbid", "12345"],
                tmdb_id="550", tvdb_id="12345")


# ===========================================================================
#   GAP 1g — the rename gate (Decision 3) and --nfo / --no-web (Decision 4)
# ===========================================================================

@pytest.mark.parametrize("cmd", BOTH_NEW)
@pytest.mark.parametrize("flag,choice", [("--yes", "yes"), ("--no-rename", "no")])
def test_rename_choice_flags(cmd, flag, choice):
    assert_argv(cmd, [flag], rename_choice=choice)


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_rename_choice_defaults_to_ask_when_neither_flag_is_given(cmd):
    """Neither flag -> "ask" (which, with no TTY, resolves to NOT renaming
    inside `_make_rename_confirm` — the smoke-hang guard)."""
    assert_argv(cmd, ["rehash"], eager_rehash=True, rename_choice="ask")


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_nfo_flag_turns_nfo_writing_on(cmd):
    assert_argv(cmd, ["--nfo"], write_nfo=True)


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_no_nfo_flag_opts_out_of_the_stamp_time_nfo(cmd):
    """IMP-U6 (D6): `--no-nfo` suppresses the default stamp-time NFO write."""
    assert_argv(cmd, ["--no-nfo"], no_nfo=True)


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_no_web_flag_disables_the_web_fallback(cmd):
    assert_argv(cmd, ["--no-web"], no_web=True)


# ===========================================================================
#   GAP 1h — `episodes <range>` (season command only)
# ===========================================================================

@pytest.mark.parametrize("rng", ["2-4", "1-1", "16.5-16.5"])
def test_season_episodes_lands_in_the_episode_range_slot(rng):
    """`episodes 2-4` -> `episode_range="2-4"`, forwarded POSITIONALLY in the
    5th slot (after split_method/split_val). The parser does not validate the
    range — an invalid one is the command's problem."""
    assert_argv(SEASON, ["episodes", rng], episode_range=rng)


def test_movie_command_treats_episodes_as_part_of_the_path():
    """The movie command has NO `episodes` token, so both tokens fall through
    into the filepath join — pinning that the two blocks are NOT symmetrical."""
    assert_argv(MOVIE, ["episodes", "2-4"],
                filepath=rf"{FAKE_ROOT}\Movies\Jane.mkv episodes 2-4")


# ===========================================================================
#   GAP 1i — full flag set + flag-order independence
# ===========================================================================

_MOVIE_FULL = ["SIZE_GB", "8", "device", "movies", "rehash", "tempdir", rf"{FAKE_ROOT}\tmp",
               "--extras", "Specials;Trailers", "--extras-size", "SIZE_MB", "500",
               "-tmdbid", "550", "--yes", "--nfo", "--no-web"]

_SEASON_FULL = ["COUNT", "4", "episodes", "2-4", "device", "series", "rehash",
                "tempdir", rf"{FAKE_ROOT}\tmp", "--extras", "Specials",
                "--extras-size", "none", "--tmdbid", "70523", "--no-rename", "--nfo"]


def test_movie_full_flag_set_parses_every_option_at_once():
    assert_argv(MOVIE, _MOVIE_FULL,
                split_method="SIZE_GB", split_val="8",
                device_id=main.DEVICE_ALIASES["movies"], eager_rehash=True,
                temp_dir=rf"{FAKE_ROOT}\tmp",
                extras=["Specials", "Trailers"], extras_size=("SIZE_MB", "500"),
                tmdb_id="550", write_nfo=True, no_web=True, rename_choice="yes")


def test_season_full_flag_set_parses_every_option_at_once():
    assert_argv(SEASON, _SEASON_FULL,
                split_method="COUNT", split_val="4", episode_range="2-4",
                device_id=main.DEVICE_ALIASES["series"], eager_rehash=True,
                temp_dir=rf"{FAKE_ROOT}\tmp",
                extras=["Specials"], extras_size=("NONE", None),
                tmdb_id="70523", write_nfo=True, rename_choice="no")


def test_movie_flag_order_does_not_change_the_kwargs():
    """The same flags in a DIFFERENT order must produce byte-identical kwargs —
    the token walk is order-independent (the path join is the only
    order-sensitive part, and no path fragments are involved here)."""
    shuffled = ["--no-web", "--nfo", "-tmdbid", "550", "--yes",
                "--extras-size", "SIZE_MB", "500", "--extras", "Specials;Trailers",
                "tempdir", rf"{FAKE_ROOT}\tmp", "rehash", "device", "movies",
                "SIZE_GB", "8"]
    _, a = parse_argv(argv_for(MOVIE, *_MOVIE_FULL))
    _, b = parse_argv(argv_for(MOVIE, *shuffled))
    assert a == b


def test_season_flag_order_does_not_change_the_kwargs():
    shuffled = ["--nfo", "--no-rename", "--tmdbid", "70523",
                "--extras-size", "none", "--extras", "Specials",
                "tempdir", rf"{FAKE_ROOT}\tmp", "rehash", "device", "series",
                "episodes", "2-4", "COUNT", "4"]
    _, a = parse_argv(argv_for(SEASON, *_SEASON_FULL))
    _, b = parse_argv(argv_for(SEASON, *shuffled))
    assert a == b


@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_flags_before_the_path_still_parse(cmd):
    """Flags may precede the path token — the walk collects path parts wherever
    they appear, so the path still arrives whole."""
    path = _HEAD[cmd][1]
    name, kw = parse_argv(["main.py", cmd, _HEAD[cmd][0], "--yes", "SIZE_MB", "700", path])
    assert name == _CMD_FUNC[cmd]
    assert kw == expected_kwargs(cmd, split_method="SIZE_MB", split_val="700",
                                rename_choice="yes")


# ===========================================================================
#   GAP 1j — paths containing spaces
# ===========================================================================

_SPACEY = rf"{FAKE_ROOT}\Movies\The Autopsy of Jane Doe (2016)\The.Autopsy.of.Jane.Doe.2016.mkv"
_SPACEY_SEASON = rf"{FAKE_ROOT}\Series\The Haunting of Hill House\Season 01"


@pytest.mark.parametrize("cmd,path", [(MOVIE, _SPACEY), (SEASON, _SPACEY_SEASON)])
def test_quoted_path_with_spaces_survives_as_one_token(cmd, path):
    """The real-world case: the shell hands a quoted path as ONE argv token, so
    the `" ".join(...)` must round-trip it unchanged."""
    name, kw = parse_argv(["main.py", cmd, _HEAD[cmd][0], path, "--yes"])
    assert name == _CMD_FUNC[cmd]
    assert kw == expected_kwargs(cmd, rename_choice="yes",
                                **{_PATH_KEY[cmd]: path})


@pytest.mark.parametrize("cmd,path", [(MOVIE, _SPACEY), (SEASON, _SPACEY_SEASON)])
def test_unquoted_path_tokens_are_rejoined_with_single_spaces(cmd, path):
    """An UNQUOTED path arrives split on spaces; the join reassembles it. This
    is exactly what `" ".join(<path parts>)` exists for."""
    tokens = path.split(" ")
    assert len(tokens) > 1
    name, kw = parse_argv(["main.py", cmd, _HEAD[cmd][0], *tokens, "--yes"])
    assert name == _CMD_FUNC[cmd]
    assert kw == expected_kwargs(cmd, rename_choice="yes",
                                **{_PATH_KEY[cmd]: path})


@pytest.mark.parametrize("cmd,path", [(MOVIE, _SPACEY), (SEASON, _SPACEY_SEASON)])
def test_spacey_path_survives_alongside_every_other_flag(cmd, path):
    """Flags interleaved around a quoted spacey path must not corrupt it."""
    extra = ["SIZE_MB", "700", "device", "movies", path, "-tmdbid", "550", "--nfo"]
    name, kw = parse_argv(["main.py", cmd, _HEAD[cmd][0], *extra])
    assert name == _CMD_FUNC[cmd]
    assert kw == expected_kwargs(
        cmd, split_method="SIZE_MB", split_val="700",
        device_id=main.DEVICE_ALIASES["movies"], tmdb_id="550", write_nfo=True,
        **{_PATH_KEY[cmd]: path})


# ===========================================================================
#   GAP 1k — the documented bare-flag edge case (deliberate parity, NOT a bug)
# ===========================================================================
#   Every value-taking token in these blocks is guarded by `if i + 1 < len(...)`.
#   When the guard fails the code does NOT `continue`, so control falls out of
#   the if/elif chain into `<path>_parts.append(arg)` — the trailing flag ends up
#   inside the path string and the value stays None. IMP-D22's -tmdbid/-tvdbid
#   arms reproduce that shape ON PURPOSE (parity with the pre-existing
#   device/tempdir/SIZE_* arms). These tests pin the ACTUAL behaviour.
# ===========================================================================

@pytest.mark.parametrize("cmd", BOTH_NEW)
@pytest.mark.parametrize("flag", ["-tmdbid", "--tmdbid"])
def test_trailing_tmdbid_with_no_value_falls_through_into_the_path(cmd, flag):
    assert_argv(cmd, [flag], tmdb_id=None,
                **{_PATH_KEY[cmd]: f"{_HEAD[cmd][1]} {flag}"})


@pytest.mark.parametrize("cmd", BOTH_NEW)
@pytest.mark.parametrize("flag", ["-tvdbid", "--tvdbid"])
def test_trailing_tvdbid_with_no_value_falls_through_into_the_path(cmd, flag):
    assert_argv(cmd, [flag], tvdb_id=None,
                **{_PATH_KEY[cmd]: f"{_HEAD[cmd][1]} {flag}"})


@pytest.mark.parametrize("cmd", BOTH_NEW)
@pytest.mark.parametrize("flag", ["device", "tempdir", "SIZE_MB", "SIZE_GB", "COUNT"])
def test_trailing_preexisting_value_flags_share_the_same_fall_through_shape(cmd, flag):
    """The PRE-EXISTING arms behave identically — which is why the new -tmdbid /
    -tvdbid arms were written this way. Pinning both sides makes the parity
    explicit rather than accidental."""
    assert_argv(cmd, [flag], **{_PATH_KEY[cmd]: f"{_HEAD[cmd][1]} {flag}"})


def test_trailing_episodes_falls_through_into_the_folder_path():
    assert_argv(SEASON, ["episodes"], episode_range=None,
                folder_path=f"{_HEAD[SEASON][1]} episodes")


@pytest.mark.parametrize("cmd", BOTH_NEW)
@pytest.mark.parametrize("flag", ["--extras", "--extras-size"])
def test_trailing_extras_flags_exit_instead_of_falling_through(cmd, flag):
    """ASYMMETRY worth knowing: the extras arm's `continue` sits OUTSIDE its
    look-ahead, so a valueless `--extras` / `--extras-size` is handed to
    `parse_extras_tokens`, which prints an error and `sys.exit(1)`s — it does
    NOT fall through into the path like every other value flag."""
    with pytest.raises(SystemExit) as exc:
        run_dispatcher(argv_for(cmd, flag))
    assert exc.value.code == 1


# ===========================================================================
#   GAP 1l — usage guard + no accidental dispatch
# ===========================================================================

@pytest.mark.parametrize("cmd", BOTH_NEW)
def test_missing_path_prints_usage_and_exits_without_calling_the_command(cmd, capsys):
    """`len(sys.argv) < 4` -> usage line + exit(1), and NO cmd_* is called."""
    with pytest.raises(SystemExit) as exc:
        run_dispatcher(["main.py", cmd, _HEAD[cmd][0]])
    assert exc.value.code == 1
    assert f"Usage: {cmd}" in capsys.readouterr().out


def test_an_unknown_verb_fires_no_command():
    """Harness sanity: a verb that matches no branch calls nothing at all, so a
    "one recorder fired" assertion elsewhere is meaningful."""
    assert run_dispatcher(["main.py", "prep_push_rep_enrich_typo", "x", "y"]) == []


def test_the_two_new_verbs_do_not_shadow_each_other():
    """`prep_push_rep_season_enrich` must not be swallowed by an earlier
    `prep_push_rep`/`prep_push_rep_enrich` branch (exact-equality dispatch)."""
    assert parse_argv(argv_for(MOVIE))[0] == "cmd_prep_push_rep_enrich"
    assert parse_argv(argv_for(SEASON))[0] == "cmd_prep_push_rep_season_enrich"
    assert parse_argv(argv_for(LEGACY_MOVIE))[0] == "cmd_prep_push_rep"
    assert parse_argv(argv_for(LEGACY_SEASON))[0] == "cmd_prep_push_rep_season"


# ===========================================================================
#   GAP 1m — REGRESSION PIN for the PRE-EXISTING blocks.
#
#   IMP-D22 added two sibling elif blocks by copying the token walk of these
#   two. These tests pin the OLD blocks' argv->kwargs contract so a future edit
#   to the new blocks cannot silently perturb the old ones.
# ===========================================================================

@pytest.mark.parametrize("cmd", [LEGACY_MOVIE, LEGACY_SEASON])
def test_regression_legacy_minimal_argv_defaults_unchanged(cmd):
    assert_argv(cmd, [])


def test_regression_prep_push_rep_full_flag_set_unchanged():
    """Every option the OLD movie autopilot accepts, in one argv. Note there are
    no enrich flags here at all — the old block must stay enrich-free."""
    assert_argv(
        LEGACY_MOVIE,
        ["SIZE_MB", "700", "device", "series", "rehash", "tempdir", rf"{FAKE_ROOT}\tmp",
         "--extras", "Specials;Trailers", "--extras-size", "8gb"],
        split_method="SIZE_MB", split_val="700",
        device_id=main.DEVICE_ALIASES["series"], eager_rehash=True,
        temp_dir=rf"{FAKE_ROOT}\tmp",
        extras=["Specials", "Trailers"], extras_size=("SIZE_GB", "8"))


def test_regression_prep_push_rep_season_full_flag_set_unchanged():
    assert_argv(
        LEGACY_SEASON,
        ["COUNT", "3", "episodes", "1-2", "device", "movies", "rehash",
         "tempdir", rf"{FAKE_ROOT}\tmp", "--extras", "A;B", "--extras-size", "none"],
        split_method="COUNT", split_val="3", episode_range="1-2",
        device_id=main.DEVICE_ALIASES["movies"], eager_rehash=True,
        temp_dir=rf"{FAKE_ROOT}\tmp",
        extras=["A", "B"], extras_size=("NONE", None))


@pytest.mark.parametrize("cmd", [LEGACY_MOVIE, LEGACY_SEASON])
def test_regression_legacy_blocks_ignore_the_new_enrich_flags(cmd):
    """The enrich-only flags are NOT understood by the old blocks — they fall
    through into the path exactly like any other unknown token. Pinning this
    proves the new arms were added to the NEW blocks only."""
    kw = assert_argv(cmd, ["--nfo", "--yes"],
                     **{_PATH_KEY[cmd]: f"{_HEAD[cmd][1]} --nfo --yes"})
    assert "write_nfo" not in kw and "rename_choice" not in kw


@pytest.mark.parametrize("cmd", [LEGACY_MOVIE, LEGACY_SEASON])
def test_regression_legacy_spacey_path_still_rejoins(cmd):
    path = _SPACEY if cmd == LEGACY_MOVIE else _SPACEY_SEASON
    tokens = path.split(" ")
    name, kw = parse_argv(["main.py", cmd, _HEAD[cmd][0], *tokens, "rehash"])
    assert name == _CMD_FUNC[cmd]
    assert kw == expected_kwargs(cmd, eager_rehash=True, **{_PATH_KEY[cmd]: path})
