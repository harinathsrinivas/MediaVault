r"""IMP-C18 — tests for the shared anime/series episode-range filter.

This file pins the season-aware episode-number extractor
`mvcommon.episode_num_from_id(child_id, base_id) -> float | None`, the single
implementation that all 5 range-filter sites (fetch / fetch_restore / batch
restore / push_group, plus the two already-correct season sites) route through.

The bug being fixed: anime season-map child IDs glue season+episode with no
separator — `ani-…-s0202` = season 02, episode 02. The old unanchored fallback
`re.search(r'(\d+(?:\.\d+)?)$', child_id)` captured the whole trailing digit run
(`0202` -> 202), so `episodes 2-3` matched nothing yet the run still claimed
success. The extractor strips `base_id` as a prefix first, then matches an
anchored `^[eExX]?(\d+(?:\.\d+)?)$`, so the leftover `02` reads as episode 2.

The helper is pure (stdlib `re` only) — no fixtures are needed; these are
straight calls and asserts.

Never touch real C:\\Media files or real library_*.json.
Run `python -m pytest` and fix failures before marking the step done.
"""

import pytest

import mvcommon


# --- The base id (season prefix) for the kuroko fixtures below. ---------------
_KUROKO_S02 = "ani-ja-2013-kurokosbasketball-s02"


@pytest.mark.parametrize(
    "child_id, base_id, expected",
    [
        # Glued sSSEE anime: leftover "02" -> episode 2 (NOT 0202=202). The core bug.
        ("ani-ja-2013-kurokosbasketball-s0202", _KUROKO_S02, 2.0),
        # Half-episode, glued: leftover "16.5".
        ("ani-ja-2013-kurokosbasketball-s0216.5", _KUROKO_S02, 16.5),
        # Separator series TV `s03e20`: leftover "e20" -> the `e` separator is consumed.
        ("tv-en-2016-strangerthings-s03e20", "tv-en-2016-strangerthings-s03", 20.0),
        # Separator anime `x` with a half-ep: leftover "x05.5" -> 5.5.
        (
            "ani-ja-2013-kurokosbasketball-s0116x05.5",
            "ani-ja-2013-kurokosbasketball-s0116",
            5.5,
        ),
        # Bare `eNN` after strip: leftover "e20" -> 20.0 (the `e` separator after strip).
        ("ani-ja-2013-kurokosbasketball-s01e20", "ani-ja-2013-kurokosbasketball-s01", 20.0),
        # Correct base is a true prefix of a glued-number anime id: leftover "07" -> 7.0.
        ("ani-ja-2006-deathnote07", "ani-ja-2006-deathnote", 7.0),
    ],
)
def test_episode_num_from_id_shapes(child_id, base_id, expected):
    """Every supported child-ID shape parses to the right episode float."""
    assert mvcommon.episode_num_from_id(child_id, base_id) == expected


def test_glued_ssee_is_episode_not_glued_number():
    """The exact original repro: s0202 must read 2.0, never 202.0."""
    ep = mvcommon.episode_num_from_id(
        "ani-ja-2013-kurokosbasketball-s0202", _KUROKO_S02
    )
    assert ep == 2.0
    assert ep != 202.0


def test_half_episode_returns_float():
    """Half-eps like …-s0216.5 parse to their fractional value (range float-compare)."""
    assert mvcommon.episode_num_from_id(
        "ani-ja-2013-kurokosbasketball-s0216.5", _KUROKO_S02
    ) == 16.5


def test_base_not_a_prefix_falls_back_to_whole_id():
    """When base_id is not a prefix, the WHOLE id is parsed against the anchored regex.

    The regex is anchored (`^…$`), so the whole-id fallback only yields a number
    when the whole id is itself a bare episode token. A real prefixed id such as
    'ani-ja-2006-deathnote07' has leading letters/dashes that the anchored pattern
    rejects, so the fallback returns None (it does NOT crash, and it does NOT
    invent 7.0 from a mid-string digit run — that would be the old buggy
    unanchored behavior). With the CORRECT base the same id parses to 7.0.
    """
    assert mvcommon.episode_num_from_id("ani-ja-2006-deathnote07", "wrong-base") is None
    assert (
        mvcommon.episode_num_from_id("ani-ja-2006-deathnote07", "ani-ja-2006-deathnote")
        == 7.0
    )


def test_whole_id_that_is_a_bare_episode_token_parses():
    """The whole-id fallback DOES parse when the id is itself a bare episode token."""
    # base not a prefix, but the whole id is a clean episode string -> parses.
    assert mvcommon.episode_num_from_id("07", "wrong-base") == 7.0
    assert mvcommon.episode_num_from_id("e20", "wrong-base") == 20.0


def test_no_match_returns_none():
    """Empty leftover (parent id) and junk ids return None — never a crash."""
    # Parent/season id == base -> empty leftover -> None.
    assert (
        mvcommon.episode_num_from_id(
            "tv-en-2016-strangerthings-s01", "tv-en-2016-strangerthings-s01"
        )
        is None
    )
    # Junk id with itself as base -> empty leftover -> None.
    assert mvcommon.episode_num_from_id("foo", "foo") is None


def test_falsy_base_parses_whole_id_without_crashing():
    """base_id=None and base_id='' both fall back to parsing the whole id (no crash)."""
    # Whole id is junk (no episode token) -> None.
    assert mvcommon.episode_num_from_id("foo", None) is None
    assert mvcommon.episode_num_from_id("foo", "") is None
    # Whole id is a bare episode token -> parses.
    assert mvcommon.episode_num_from_id("07", None) == 7.0
    assert mvcommon.episode_num_from_id("07", "") == 7.0


def test_return_type_is_float_or_none():
    """Contract: the helper returns `float | None` and nothing else."""
    hit = mvcommon.episode_num_from_id("ani-ja-2013-kurokosbasketball-s0202", _KUROKO_S02)
    miss = mvcommon.episode_num_from_id("foo", "foo")
    assert isinstance(hit, float)
    assert miss is None
