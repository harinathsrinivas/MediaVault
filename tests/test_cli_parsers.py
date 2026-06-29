"""Unit tests for the extracted CLI parsers and cmd_replace not-found path.

Coverage:
  - main.parse_push_group_args  (pure; no fixtures needed)
  - mainfetch.parse_fetch_args  (pure; no fixtures needed)
  - main.cmd_replace not-found  (library I/O -> sandbox + capsys)
"""
import json
import pytest
import main
import mainfetch


# ===========================================================================
# parse_push_group_args
# ===========================================================================

class TestParsePushGroupArgs:
    """Tests for main.parse_push_group_args(args).

    The parser takes sys.argv[2:] (tokens after the verb). It returns
    (group_id, method, val, ep_range, dev, eager, tdir) on success, or
    prints an error and calls sys.exit(1) on bad input.
    """

    # --- success cases ---

    def test_full_invocation_returns_correct_tuple(self):
        """All recognised tokens present: SIZE_GB + device + episodes + rehash + tempdir."""
        args = [
            "myshow",
            "SIZE_GB", "8",
            "episodes", "1-3",
            "device", "series",
            "rehash",
            "tempdir", "D:/tmp",
        ]
        result = main.parse_push_group_args(args)
        assert result == ("myshow", "SIZE_GB", "8", "1-3", "series", True, "D:/tmp")

    def test_group_id_only_returns_defaults(self):
        """Minimal invocation: just a group id, all optionals default."""
        result = main.parse_push_group_args(["myshow"])
        assert result == ("myshow", None, None, None, None, False, None)

    def test_size_mb_is_recognised(self):
        """SIZE_MB sets method correctly."""
        result = main.parse_push_group_args(["g", "SIZE_MB", "500"])
        assert result == ("g", "SIZE_MB", "500", None, None, False, None)

    def test_count_is_recognised(self):
        """COUNT sets method correctly."""
        result = main.parse_push_group_args(["g", "COUNT", "3"])
        assert result == ("g", "COUNT", "3", None, None, False, None)

    def test_rehash_sets_eager_true(self):
        """rehash token flips eager to True; no value consumed."""
        result = main.parse_push_group_args(["g", "rehash"])
        group_id, method, val, ep_range, dev, eager, tdir = result
        assert eager is True
        assert group_id == "g"

    def test_unknown_token_is_silently_skipped(self):
        """Typo'd / unknown token is ignored — mirrors push parser behaviour.

        This verifies the no-hang guarantee: the OLD looping behaviour would
        spin on an unrecognised token; the new parser increments `i` and skips
        it, so the call returns immediately with ep_range=None.
        """
        result = main.parse_push_group_args(["myshow", "episdoes", "1-3"])
        group_id, method, val, ep_range, dev, eager, tdir = result
        assert group_id == "myshow"
        assert ep_range is None  # "episdoes" skipped, "1-3" also skipped as next unknown token

    # --- fail-fast cases: trailing value-keyword must raise SystemExit, not hang ---

    def test_empty_args_raises_system_exit(self):
        """No args at all -> usage message + exit(1)."""
        with pytest.raises(SystemExit):
            main.parse_push_group_args([])

    def test_trailing_size_gb_raises_system_exit(self):
        """SIZE_GB with no following value must exit(1), not loop infinitely."""
        with pytest.raises(SystemExit):
            main.parse_push_group_args(["g", "SIZE_GB"])

    def test_trailing_size_mb_raises_system_exit(self):
        """SIZE_MB with no following value must exit(1)."""
        with pytest.raises(SystemExit):
            main.parse_push_group_args(["g", "SIZE_MB"])

    def test_trailing_device_raises_system_exit(self):
        """'device' keyword with no following value must exit(1)."""
        with pytest.raises(SystemExit):
            main.parse_push_group_args(["g", "SIZE_GB", "8", "device"])

    def test_trailing_episodes_raises_system_exit(self):
        """'episodes' keyword with no following value must exit(1)."""
        with pytest.raises(SystemExit):
            main.parse_push_group_args(["g", "g-id", "episodes"])

    def test_trailing_tempdir_raises_system_exit(self):
        """'tempdir' keyword with no following value must exit(1)."""
        with pytest.raises(SystemExit):
            main.parse_push_group_args(["g", "tempdir"])


# ===========================================================================
# parse_fetch_args
# ===========================================================================

class TestParseFetchArgs:
    """Tests for mainfetch.parse_fetch_args(argv).

    The parser takes the full argv list (argv[0] = script name, argv[1] = 'fetch',
    argv[2] = media id, …). It returns (mid, epr) on success or sys.exit(1).
    """

    # --- success cases ---

    def test_id_only_returns_mid_and_none(self):
        """Minimal valid invocation: script + verb + id."""
        mid, epr = mainfetch.parse_fetch_args(["mainfetch.py", "fetch", "tv-x"])
        assert mid == "tv-x"
        assert epr is None

    def test_episodes_range_is_parsed(self):
        """Full invocation: id + episodes + range."""
        mid, epr = mainfetch.parse_fetch_args(
            ["mainfetch.py", "fetch", "tv-x", "episodes", "1-3"]
        )
        assert mid == "tv-x"
        assert epr == "1-3"

    def test_extra_tokens_beyond_range_ignored(self):
        """Tokens after the range are tolerated (parser stops after reading range)."""
        mid, epr = mainfetch.parse_fetch_args(
            ["mainfetch.py", "fetch", "tv-x", "episodes", "4-6", "extra"]
        )
        assert mid == "tv-x"
        assert epr == "4-6"

    # --- fail-fast cases ---

    def test_fetch_with_no_id_raises_system_exit(self):
        """Regression: 'fetch' with no id (len < 3) caused IndexError before the fix.
        Now must raise SystemExit via sys.exit(1)."""
        with pytest.raises(SystemExit):
            mainfetch.parse_fetch_args(["mainfetch.py", "fetch"])

    def test_too_few_args_raises_system_exit(self):
        """Only script name, no verb or id."""
        with pytest.raises(SystemExit):
            mainfetch.parse_fetch_args(["mainfetch.py"])

    def test_empty_argv_raises_system_exit(self):
        """Completely empty argv."""
        with pytest.raises(SystemExit):
            mainfetch.parse_fetch_args([])

    def test_wrong_verb_raises_system_exit(self):
        """Wrong verb ('pull' instead of 'fetch') must exit(1)."""
        with pytest.raises(SystemExit):
            mainfetch.parse_fetch_args(["mainfetch.py", "pull", "tv-x"])


# ===========================================================================
# parse_extras_tokens
# ===========================================================================

class TestParseExtrasTokens:
    """Tests for main.parse_extras_tokens(tokens).

    The function takes the already-extracted extras-related tokens (not the
    full argv) and returns {'folders': [...], 'extras_size': tuple|None}.
    """

    def test_empty_returns_defaults(self):
        result = main.parse_extras_tokens([])
        assert result == {"folders": [], "extras_size": None}

    def test_single_extras_single_folder(self):
        result = main.parse_extras_tokens(["--extras", "C:/Specials"])
        assert result["folders"] == ["C:/Specials"]
        assert result["extras_size"] is None

    def test_extras_semicolon_split(self):
        """A semicolon-separated value inside --extras is split into multiple folders."""
        result = main.parse_extras_tokens(["--extras", "A;B"])
        assert result["folders"] == ["A", "B"]

    def test_multiple_extras_flags_accumulate(self):
        """Repeated --extras flags accumulate; semicolons in each are also split."""
        result = main.parse_extras_tokens(["--extras", "A;B", "--extras", "C"])
        assert result["folders"] == ["A", "B", "C"]

    def test_extras_size_mb(self):
        result = main.parse_extras_tokens(["--extras-size", "9900mb"])
        assert result["extras_size"] == ("SIZE_MB", "9900")
        assert result["folders"] == []

    def test_extras_size_gb(self):
        result = main.parse_extras_tokens(["--extras-size", "4gb"])
        assert result["extras_size"] == ("SIZE_GB", "4")

    def test_extras_size_none_returns_none_sentinel(self):
        """'none' maps to the ('NONE', None) sentinel (not Python None)."""
        result = main.parse_extras_tokens(["--extras-size", "none"])
        assert result["extras_size"] == ("NONE", None)

    def test_combined_folders_and_size(self):
        """Acceptance test: the exact invocation from the task spec."""
        result = main.parse_extras_tokens(["--extras", "A;B", "--extras", "C", "--extras-size", "9900mb"])
        assert result == {"folders": ["A", "B", "C"], "extras_size": ("SIZE_MB", "9900")}


# ===========================================================================
# cmd_replace — not-found path
# ===========================================================================

def test_cmd_replace_unknown_id_reports_not_found(sandbox, capsys):
    """cmd_replace with an id absent from the library must return False and
    print a clear error message.  Uses the `sandbox` fixture so all library
    reads/writes go to temp-dir JSON files — never real C:\\Media or
    real library_*.json.
    """
    # Seed an empty library (no entries).
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    result = main.cmd_replace("bad-id")

    assert result is False

    out = capsys.readouterr().out
    assert "not found in library" in out


def test_cmd_replace_unknown_id_with_unrelated_entry(sandbox, capsys):
    """cmd_replace with an id that is not in the library, even when OTHER
    entries are present, returns False and prints the not-found message.
    """
    # Seed a library that has one entry but NOT the id we will query.
    entry = {
        "mov-en-2020-inception": {
            "status": "onboarded",
            "uploaded": True,
            "folder_path": str(sandbox["media_dir"]),
            "filename": "inception.mkv",
            "type": "movie",
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    result = main.cmd_replace("mov-en-2020-tenet")

    assert result is False

    out = capsys.readouterr().out
    assert "not found in library" in out
    # The bad id should appear in the error message.
    assert "mov-en-2020-tenet" in out
