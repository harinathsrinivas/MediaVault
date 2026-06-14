"""Pure unit tests for mainfetch.profile_for_id() and the CHROME_PROFILES/ID_PREFIX_PROFILE map constants.

Never touch real C:\\Media files or real library_*.json.
"""
import mainfetch


class TestProfileForId:

    def test_anime_standard(self):
        assert mainfetch.profile_for_id("ani-ja-2006-deathnote01") == "anime"

    def test_anime_half_ep(self):
        assert mainfetch.profile_for_id("ani-ja-2012-kurokosbasketball-s0325.5") == "anime"

    def test_tv_standard(self):
        assert mainfetch.profile_for_id("tv-en-2016-strangerthings-s01e03") == "tv"

    def test_tv_no_s_prefix(self):
        assert mainfetch.profile_for_id("tv-en-2019-chernobyle01") == "tv"

    def test_movie(self):
        assert mainfetch.profile_for_id("mov-en-2025-f1") == "movies"

    def test_unknown_prefix_fallback(self):
        assert mainfetch.profile_for_id("weird-legacy-id") == mainfetch.DEFAULT_PROFILE == "movies"

    def test_empty_string_fallback(self):
        assert mainfetch.profile_for_id("") == "movies"

    def test_regression_ani_not_tv(self):
        """Key regression guard: ani-* and tv-* must NOT route to the same profile."""
        assert (
            mainfetch.profile_for_id("ani-ja-2006-deathnote01")
            != mainfetch.profile_for_id("tv-en-2016-strangerthings-s01e03")
        )

    def test_map_integrity_all_keys_present(self):
        assert "movies" in mainfetch.CHROME_PROFILES
        assert "tv" in mainfetch.CHROME_PROFILES
        assert "anime" in mainfetch.CHROME_PROFILES

    def test_map_integrity_anime_path(self):
        assert mainfetch.CHROME_PROFILES["anime"].endswith("ChromeProfile_Anime")

    def test_map_integrity_prefix_keys_in_profiles(self):
        for _prefix, profile_key in mainfetch.ID_PREFIX_PROFILE:
            assert profile_key in mainfetch.CHROME_PROFILES, (
                f"Profile key {profile_key!r} from ID_PREFIX_PROFILE not found in CHROME_PROFILES"
            )

    def test_map_integrity_default_in_profiles(self):
        assert mainfetch.DEFAULT_PROFILE in mainfetch.CHROME_PROFILES
