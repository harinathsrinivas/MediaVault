"""IMP-U6 — pin the shared provider-token detection contract.

`mvcommon.find_provider_tokens` / `mvcommon.has_tmdb_token` are the ONE parser
for `{tmdb-…}` / `[tmdb-…]` / `[tmdbid-…]` / `[tmdbid=…]` tokens (any casing).
This vocabulary drifted three times (IMP-C18, C22, C23) because a second copy
of the parsing logic existed and diverged. `main._has_tmdb_token` is now a
thin wrapper over `mvcommon.has_tmdb_token` (Step 2) — this file tests BOTH
the shared helper directly and the wrapper's wiring, plus a drift-pin test
asserting they never disagree, so a future regression in either place is
caught here.

Pure string-in / bool-or-list-out unit tests: no library or filesystem I/O,
no fixtures, no sandbox. Never touches real C:\\Media files or real
library_*.json (moot here, but the rule stands). Run `pytest -q` and fix
failures before marking the step done.
"""
import main
import mvcommon


# ---------------------------------------------------------------------------
# The Step 1 Acceptance-list input set, reused for the drift-pin test so
# main._has_tmdb_token and mvcommon.has_tmdb_token are compared over exactly
# the same names the individual (a)-(i) tests exercise.
# ---------------------------------------------------------------------------
ACCEPTANCE_NAMES = [
    "{tmdb-603692}",
    "[tmdb-603692]",
    "[tmdbid-603692]",
    "{TMDB-69590}",
    "[tmdbid=603692]",
    "Peaky.Blinders.S06.2022.2160p.iP.WEB-DL.x265.10bit.HDR.HLG.DDP5.1-FLUX"
    "[rartv] [tmdbid-60574]",
    "{tmdb-123]",
    "[tmdb-123}",
    "Show [tvdbid-266189] [tmdbid-70523]",
    "Show [tvdbid-266189]",
    "{tmdb-123] [tmdbid-456}",
    "",
    None,
]


def test_a_curly_tmdb_token_found():
    assert mvcommon.has_tmdb_token("{tmdb-603692}") is True


def test_b_square_tmdb_bare_tag_token_found():
    assert mvcommon.has_tmdb_token("[tmdb-603692]") is True


def test_c_square_tmdbid_tag_token_found():
    assert mvcommon.has_tmdb_token("[tmdbid-603692]") is True


def test_d_case_insensitive_curly_tmdb_token_found():
    # Run (2002) {TMDB-69590} is a real folder in the user's library — IMP-C23
    # exists because upper-case TMDB was once missed by a divergent copy.
    assert mvcommon.has_tmdb_token("Run (2002) {TMDB-69590}") is True


def test_e_emby_equals_variant_found():
    assert mvcommon.has_tmdb_token("[tmdbid=603692]") is True


def test_f_bare_release_group_bracket_does_not_match_and_only_one_token_found():
    name = ("Peaky.Blinders.S06.2022.2160p.iP.WEB-DL.x265.10bit.HDR.HLG."
            "DDP5.1-FLUX[rartv] [tmdbid-60574]")
    tokens = mvcommon.find_provider_tokens(name)
    assert len(tokens) == 1
    assert tokens[0]["provider"] == "tmdb"
    assert tokens[0]["id"] == "60574"
    assert "[rartv]" not in tokens[0]["match"]


def test_g_mismatched_brackets_not_found():
    assert mvcommon.find_provider_tokens("{tmdb-123]") == []
    assert mvcommon.find_provider_tokens("[tmdb-123}") == []
    assert mvcommon.has_tmdb_token("{tmdb-123]") is False
    assert mvcommon.has_tmdb_token("[tmdb-123}") is False


def test_h_tvdb_and_tmdb_tokens_both_returned_and_has_tmdb_true():
    name = "Show [tvdbid-266189] [tmdbid-70523]"
    tokens = mvcommon.find_provider_tokens(name)
    assert len(tokens) == 2
    providers = {token["provider"] for token in tokens}
    assert providers == {"tvdb", "tmdb"}
    assert mvcommon.has_tmdb_token(name) is True


def test_i_tvdb_only_does_not_count_as_tmdb():
    name = "Show [tvdbid-266189]"
    tokens = mvcommon.find_provider_tokens(name)
    assert len(tokens) == 1
    assert tokens[0]["provider"] == "tvdb"
    assert mvcommon.has_tmdb_token(name) is False


def test_compound_cross_family_token_never_spans_both_brackets():
    # The exact case that separated the two Step 1 candidates: the rejected
    # candidate matched this as a single token spanning the whole string.
    name = "{tmdb-123] [tmdbid-456}"
    assert mvcommon.find_provider_tokens(name) == []
    assert mvcommon.has_tmdb_token(name) is False


def test_span_integrity_across_multi_token_name():
    name = "Show [tvdbid-266189] [tmdbid-70523] extra text"
    tokens = mvcommon.find_provider_tokens(name)
    assert len(tokens) == 2
    for token in tokens:
        start, end = token["span"]
        assert name[start:end] == token["match"]


def test_tokens_returned_non_overlapping_left_to_right():
    name = "{tmdb-1} [tvdbid-2] [tmdbid-3]"
    tokens = mvcommon.find_provider_tokens(name)
    assert len(tokens) == 3
    spans = [token["span"] for token in tokens]
    assert spans == sorted(spans)
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        assert prev_end <= next_start


def test_canonical_tmdb_format_constant():
    # Hardcoding the literal is the POINT of these two tests — they are the one
    # place that pins what the constant expands to. Everywhere else, build the
    # expected name from the constant so the emit format stays a one-line flip.
    # `tmdb` (not `tmdbid`) is load-bearing: Plex ignores the `tmdbid` keyword,
    # while `[tmdb-…]` is read by Plex, Emby AND Jellyfin (empirically verified).
    assert mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=603692) == "[tmdb-603692]"
    assert mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id="603692") == "[tmdb-603692]"


def test_canonical_tvdb_format_constant():
    assert mvcommon.CANONICAL_TVDB_TOKEN_FMT.format(id=0) == "[tvdb-0]"
    assert mvcommon.CANONICAL_TVDB_TOKEN_FMT.format(id="000000") == "[tvdb-000000]"


def test_has_tmdb_token_tolerates_none_and_empty_string():
    assert mvcommon.has_tmdb_token(None) is False
    assert mvcommon.has_tmdb_token("") is False


def test_drift_pin_main_wrapper_matches_mvcommon_across_acceptance_names():
    # Mirrors the exact drift-pin pattern IMP-C23 added — same idea, now
    # enforced structurally (main._has_tmdb_token is a thin wrapper, Step 2)
    # AND tested, so a future regression in the wrapper wiring is caught here.
    for name in ACCEPTANCE_NAMES:
        assert main._has_tmdb_token(name) == mvcommon.has_tmdb_token(name), name
