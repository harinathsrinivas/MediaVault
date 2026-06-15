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


# ---------------------------------------------------------------------------
# Integration-level tests: resolve_targets at the library boundary
# ---------------------------------------------------------------------------
# These tests use the `sandbox` fixture (library I/O redirect) to drive
# mainfetch.resolve_targets against a seeded in-memory library.  No real
# Selenium, ADB, or file I/O beyond the sandbox tmp_path happens here.
# ---------------------------------------------------------------------------

import json
import mainfetch

# Season/episode ids for the kuroko anime integration test (sSSEE glued shape).
_K_SEASON_ID = "ani-ja-2013-kurokosbasketball-s02"
_K_EP_IDS = [f"{_K_SEASON_ID}{n:02d}" for n in (1, 2, 3)]  # s0201, s0202, s0203
_K_HALF_EP_ID = f"{_K_SEASON_ID}16.5"  # ep 16.5 — must be EXCLUDED by range 2-3

# Season/episode ids for the tv-series cross-format guard.
_TV_SEASON_ID = "tv-en-2016-strangerthings-s03"
_TV_EP_IDS = [f"{_TV_SEASON_ID}e0{n}" for n in (1, 2, 3, 4)]  # e01..e04


def _minimal_leaf(ep_id, parent_id, folder_path):
    """Return the minimum set of fields that resolve_targets needs (no real file)."""
    return {
        "short_id": mvcommon.generate_short_id(ep_id),
        "filename": f"{ep_id}.mkv",
        "folder_path": folder_path,
        "status": "onboarded",
        "uploaded": True,
        "parent_id": parent_id,
    }


def _seed_libs(sandbox, anime_lib, series_lib=None):
    """Write all three lib JSON files; libs not provided default to {}."""
    sandbox["lib_anime"].write_text(json.dumps(anime_lib), encoding="utf-8")
    sandbox["lib_series"].write_text(
        json.dumps(series_lib if series_lib is not None else {}), encoding="utf-8"
    )
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")


def test_resolve_targets_kuroko_range_2_3_selects_exactly_two(sandbox):
    """Regression pin for IMP-C18: `episodes 2-3` over a glued sSSEE season
    must resolve to exactly episodes s0202 and s0203 — not 0 (old bug) and
    not 3 (would include the half-ep s0216.5).

    This exercises the EXACT original repro shape end-to-end through
    mainfetch.resolve_targets and the shared mvcommon.episode_num_from_id helper.
    """
    folder = str(sandbox["media_dir"])

    # Build the anime library: 3 normal eps + 1 half-ep, all under the season_map.
    all_children = _K_EP_IDS + [_K_HALF_EP_ID]
    anime_lib = {
        _K_SEASON_ID: {
            "type": "season_map",
            "folder_path": folder,
            "total_episodes": len(all_children),
            "children": all_children,
        },
    }
    for ep_id in all_children:
        anime_lib[ep_id] = _minimal_leaf(ep_id, _K_SEASON_ID, folder)
    _seed_libs(sandbox, anime_lib)

    results = mainfetch.resolve_targets(_K_SEASON_ID, ep_range="2-3")

    # Exactly 2 entries returned — not 0 (the old bug) and not 3 (half-ep included).
    assert len(results) == 2, (
        f"Expected 2 entries for ep_range='2-3', got {len(results)}: "
        f"{[r.get('filename') for r in results]}"
    )

    # The two entries correspond to ep 2 and ep 3 (check by parent_id so we are
    # not testing filename construction — filename is an implementation detail).
    result_filenames = {r["filename"] for r in results}
    assert f"{_K_SEASON_ID}02.mkv" in result_filenames, (
        f"s0202 (ep 2) must be in result; got: {result_filenames}"
    )
    assert f"{_K_SEASON_ID}03.mkv" in result_filenames, (
        f"s0203 (ep 3) must be in result; got: {result_filenames}"
    )

    # Half-ep 16.5 must NOT appear.
    assert f"{_K_HALF_EP_ID}.mkv" not in result_filenames, (
        f"Half-ep {_K_HALF_EP_ID} must be excluded by range 2-3"
    )


def test_resolve_targets_kuroko_old_bug_range_202_203_returns_empty(sandbox):
    """The old unanchored fallback read s0202 as episode 202, so range '202-203'
    would have incorrectly matched. After the fix, 202-203 returns [] (plus a
    ⚠️ warning) because no episode number >= 202 exists in the season.

    This test pins that the bug-reliant behaviour is gone — 202-203 must NOT
    silently return 2 episodes any more.
    """
    folder = str(sandbox["media_dir"])
    all_children = _K_EP_IDS + [_K_HALF_EP_ID]
    anime_lib = {
        _K_SEASON_ID: {
            "type": "season_map",
            "folder_path": folder,
            "total_episodes": len(all_children),
            "children": all_children,
        },
    }
    for ep_id in all_children:
        anime_lib[ep_id] = _minimal_leaf(ep_id, _K_SEASON_ID, folder)
    _seed_libs(sandbox, anime_lib)

    results = mainfetch.resolve_targets(_K_SEASON_ID, ep_range="202-203")

    assert results == [], (
        f"Old-bug range '202-203' must return [] after the fix; got {len(results)} entries"
    )


def test_resolve_targets_tv_series_separator_range_2_3(sandbox):
    """Cross-format guard: tv- series with eNN-separator child ids must also
    resolve correctly.  ep_range='2-3' over a 4-episode season (e01..e04) must
    return exactly 2 entries (e02 and e03).
    """
    folder = str(sandbox["media_dir"])

    series_lib = {
        _TV_SEASON_ID: {
            "type": "season_map",
            "folder_path": folder,
            "total_episodes": len(_TV_EP_IDS),
            "children": list(_TV_EP_IDS),
        },
    }
    for ep_id in _TV_EP_IDS:
        series_lib[ep_id] = _minimal_leaf(ep_id, _TV_SEASON_ID, folder)
    _seed_libs(sandbox, anime_lib={}, series_lib=series_lib)

    results = mainfetch.resolve_targets(_TV_SEASON_ID, ep_range="2-3")

    assert len(results) == 2, (
        f"Expected 2 entries for tv-series ep_range='2-3', got {len(results)}: "
        f"{[r.get('filename') for r in results]}"
    )
    result_filenames = {r["filename"] for r in results}
    assert f"{_TV_SEASON_ID}e02.mkv" in result_filenames
    assert f"{_TV_SEASON_ID}e03.mkv" in result_filenames
