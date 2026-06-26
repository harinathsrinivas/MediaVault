"""Permanent tests for the trivia backfill backend (IMP-E16/A5).

The "fetch trivia" feature web-searches each title's behind-the-scenes facts via
EXA, distills 2-4 SHORT, source-tagged facts via GROQ, caches them in the
gitignored mvextra.json keyed by tmdb_id, and the hover dossier merges that cache
(read-only). These tests pin the backend contract:

  (a) host -> clean source-name mapping (imdb.com -> IMDb, subdomains, unknown ->
      bare domain, empty -> "web"); GROQ-source normalization.
  (b) exa_search_trivia POSTs the title+year trivia query and parses results;
      degrades to [] on no-key / HTTP error / network error (never raises).
  (c) _groq_chat sends the REQUIRED Cloudflare User-Agent + auth; groq_distill_trivia
      parses a strict-JSON reply into [{text, source}], extracts JSON amid prose,
      falls back to line-splitting (source="web") on a malformed reply, caps at 4,
      and degrades to [] (no key / no snippets / GROQ error).
  (d) the mvextra.json cache helpers round-trip + are atomic + degrade on a malformed
      file; the freshness rule (~30 days).
  (e) cmd_fetch_trivia writes mvextra.json keyed by tmdb_id, DEDUPES by tmdb_id (a
      show's episodes => ONE fetch), honors the freshness skip + --force, reports the
      no-results / failed / no-tmdb buckets, and NEVER mutates library_*.json.

ALL mocked — NO real network:
  * EXA / GROQ HTTP: monkeypatch main.requests.post (the requests seam) OR the
    higher-level main.exa_search_trivia / main.groq_distill_trivia / main._groq_chat.
  * The cache file is redirected to tmp_path via the `extra_cache` fixture (which
    hard-guards against the real repo-root mvextra.json) and the EXA/GROQ key getters
    are faked so the command does not bail.
All over the LOCAL_ROOT-hermetic `sandbox` fixture (never real C:\\Media /
library_*.json / the real mvextra.json).
"""
import json

import pytest

import main
import mvcommon


# ---------------------------------------------------------------------------
# Minimal requests.Response stand-in for the EXA/GROQ HTTP seam.
# ---------------------------------------------------------------------------

class _FakeResp:
    """.status_code + .json() (raises ValueError when no json, like a non-JSON body)."""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


# Canned EXA + GROQ payloads.
_EXA_RESULTS = {
    "results": [
        {"title": "Inception Trivia", "url": "https://www.imdb.com/title/tt1375666/trivia",
         "text": "The spinning top was Cobb's totem in the script."},
        {"title": "10 Inception Facts", "url": "https://screenrant.com/inception-facts/",
         "text": "The rotating-hallway fight used a giant practical set."},
    ]
}

_GROQ_JSON = (
    '[{"text": "The hallway fight was shot in a giant rotating set.", "source": "ScreenRant"}, '
    '{"text": "The spinning-top ending was deliberately ambiguous.", "source": "IMDb"}]'
)


# ---------------------------------------------------------------------------
# extra_cache fixture — redirect mvextra.json to tmp_path + fake the EXA/GROQ keys
# + neutralize the polite throttle so the bulk loop is instant. Hard-guards the path.
# ---------------------------------------------------------------------------

@pytest.fixture()
def extra_cache(tmp_path, monkeypatch):
    """Redirect EXTRA_CACHE_PATH to tmp_path and fake the EXA / GROQ keys so
    cmd_fetch_trivia runs.

    HARD GUARD (mirrors the sandbox fixture's C:\\Media guard): the redirected
    mvextra.json must live under tmp_path and must NOT be the real repo-root
    mvextra.json — a future regression that forgets the patch would write a real
    cache file at the repo root, which this assertion catches.

    Also stubs main.time.sleep so the loop's polite inter-title throttle is instant.

    Yields the Path to the sandbox mvextra.json so a test can seed/inspect it.
    """
    cache_path = tmp_path / "mvextra.json"
    assert str(cache_path) != main.EXTRA_CACHE_PATH, "must redirect away from the real mvextra.json"
    assert "PycharmProjects" not in str(cache_path) or str(tmp_path) in str(cache_path)

    monkeypatch.setattr(main, "EXTRA_CACHE_PATH", str(cache_path))
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "EXA-TEST-KEY")
    monkeypatch.setattr(mvcommon, "groq_api_key", lambda: "GROQ-TEST-KEY")
    # Neutralize the inter-title throttle (TRIVIA_THROTTLE_SECONDS) so tests are fast.
    monkeypatch.setattr(main.time, "sleep", lambda *_a, **_k: None)
    yield cache_path


# ===========================================================================
# (a) host -> source-name mapping + GROQ-source normalization
# ===========================================================================

def test_host_to_source_known_hosts():
    assert main._trivia_host_to_source("https://www.imdb.com/title/tt1/trivia") == "IMDb"
    assert main._trivia_host_to_source("https://screenrant.com/inception-facts/") == "ScreenRant"
    assert main._trivia_host_to_source("https://www.reddit.com/r/movies/x") == "Reddit"
    assert main._trivia_host_to_source("https://en.wikipedia.org/wiki/Inception") == "Wikipedia"


def test_host_to_source_subdomain_match():
    """A subdomain of a known host still maps to the clean name."""
    assert main._trivia_host_to_source("https://m.imdb.com/x") == "IMDb"
    assert main._trivia_host_to_source("https://old.reddit.com/r/x") == "Reddit"


def test_host_to_source_unknown_degrades_to_bare_domain():
    assert main._trivia_host_to_source("https://example.org/some/page") == "example.org"
    # leading www. is stripped from an unknown host too
    assert main._trivia_host_to_source("https://www.somefansite.net/x") == "somefansite.net"


def test_host_to_source_empty_or_unparseable():
    assert main._trivia_host_to_source("") == "web"
    assert main._trivia_host_to_source(None) == "web"
    assert main._trivia_host_to_source("http://") == "web"  # no host


def test_normalize_source_variants():
    assert main._normalize_source("IMDb") == "IMDb"            # already clean -> kept
    assert main._normalize_source("imdb.com") == "IMDb"        # bare host -> mapped
    assert main._normalize_source("https://screenrant.com/x") == "ScreenRant"  # url -> mapped
    assert main._normalize_source("") == "web"                 # blank -> fallback
    assert main._normalize_source(None) == "web"
    assert main._normalize_source("Reddit") == "Reddit"


# ===========================================================================
# (b) exa_search_trivia — POSTs the query, parses results, degrades to []
# ===========================================================================

def test_exa_search_trivia_posts_and_parses(monkeypatch):
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "EXA-KEY")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kw):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResp(200, _EXA_RESULTS)

    monkeypatch.setattr(main.requests, "post", fake_post)
    out = main.exa_search_trivia("Inception", 2010)

    assert captured["url"] == "https://api.exa.ai/search"
    assert captured["headers"]["x-api-key"] == "EXA-KEY"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["json"]["query"] == "Inception 2010 movie trivia behind the scenes facts"
    assert captured["json"]["numResults"] == 4
    assert captured["json"]["contents"]["text"]["maxCharacters"] == 800
    assert len(out) == 2
    assert out[0]["url"] == "https://www.imdb.com/title/tt1375666/trivia"
    assert out[0]["text"].startswith("The spinning top")


def test_exa_search_trivia_no_year_query(monkeypatch):
    """With no year, the year segment is dropped from the query (no 'None')."""
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "EXA-KEY")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kw):
        captured["json"] = json
        return _FakeResp(200, {"results": []})

    monkeypatch.setattr(main.requests, "post", fake_post)
    main.exa_search_trivia("Inception", None)
    assert captured["json"]["query"] == "Inception movie trivia behind the scenes facts"


def test_exa_search_trivia_no_key_returns_empty(monkeypatch):
    """No EXA key -> [] without any POST."""
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "")
    monkeypatch.setattr(main.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no key -> no POST")))
    assert main.exa_search_trivia("Inception", 2010) == []


def test_exa_search_trivia_http_error_returns_empty(monkeypatch):
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "EXA-KEY")
    monkeypatch.setattr(main.requests, "post", lambda *a, **k: _FakeResp(500, None))
    assert main.exa_search_trivia("Inception", 2010) == []


def test_exa_search_trivia_network_error_returns_empty(monkeypatch):
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "EXA-KEY")

    def boom(*a, **k):
        raise ConnectionError("simulated EXA network failure")

    monkeypatch.setattr(main.requests, "post", boom)
    assert main.exa_search_trivia("Inception", 2010) == []


def test_exa_search_trivia_bad_payload_returns_empty(monkeypatch):
    """A 200 whose body has no results list -> []."""
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "EXA-KEY")
    monkeypatch.setattr(main.requests, "post", lambda *a, **k: _FakeResp(200, {"unexpected": True}))
    assert main.exa_search_trivia("Inception", 2010) == []


# ===========================================================================
# (c) GROQ — required UA, strict-JSON parse, prose extraction, line fallback, caps
# ===========================================================================

def test_groq_chat_sends_required_user_agent(monkeypatch):
    """The GROQ POST MUST carry a browser-ish User-Agent (Cloudflare 403s error 1010
    without it) + the bearer auth + the model. Regression guard for the verified
    header set."""
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kw):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResp(200, {"choices": [{"message": {"content": "hello"}}]})

    monkeypatch.setattr(main.requests, "post", fake_post)
    out = main._groq_chat([{"role": "user", "content": "x"}], "GROQ-KEY")

    assert out == "hello"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert "Mozilla/5.0" in captured["headers"]["User-Agent"]
    assert captured["headers"]["Authorization"] == "Bearer GROQ-KEY"
    assert captured["json"]["model"] == "llama-3.3-70b-versatile"


def test_groq_chat_http_error_returns_none(monkeypatch):
    monkeypatch.setattr(main.requests, "post", lambda *a, **k: _FakeResp(403, None))
    assert main._groq_chat([{"role": "user", "content": "x"}], "K") is None


def test_groq_chat_bad_shape_returns_none(monkeypatch):
    """A 200 missing choices[0].message.content -> None (not a crash)."""
    monkeypatch.setattr(main.requests, "post", lambda *a, **k: _FakeResp(200, {"no": "choices"}))
    assert main._groq_chat([{"role": "user", "content": "x"}], "K") is None


def _snips():
    return [{"title": "t", "url": "https://www.imdb.com/x", "text": "some page text"}]


def test_groq_distill_parses_strict_json(monkeypatch):
    """A canned strict-JSON reply parses into [{text, source}], sources normalized."""
    monkeypatch.setattr(mvcommon, "groq_api_key", lambda: "GROQ-KEY")
    monkeypatch.setattr(main, "_groq_chat", lambda messages, api_key, **k: _GROQ_JSON)

    facts = main.groq_distill_trivia("Inception", 2010, _snips())
    assert facts == [
        {"text": "The hallway fight was shot in a giant rotating set.", "source": "ScreenRant"},
        {"text": "The spinning-top ending was deliberately ambiguous.", "source": "IMDb"},
    ]


def test_groq_distill_extracts_json_amid_prose(monkeypatch):
    """GROQ sometimes wraps the array in prose / markdown fences; the JSON array is
    still extracted (defensive parse)."""
    monkeypatch.setattr(mvcommon, "groq_api_key", lambda: "GROQ-KEY")
    noisy = ('Sure! Here are the facts:\n```json\n'
             '[{"text": "Fact one is genuinely interesting.", "source": "IMDb"}]\n```\nHope that helps!')
    monkeypatch.setattr(main, "_groq_chat", lambda *a, **k: noisy)

    facts = main.groq_distill_trivia("X", 2000, _snips())
    assert facts == [{"text": "Fact one is genuinely interesting.", "source": "IMDb"}]


def test_groq_distill_malformed_falls_back_to_lines(monkeypatch):
    """A non-JSON reply falls back to line-splitting (bullets stripped), each tagged
    source='web' — the documented graceful fallback."""
    monkeypatch.setattr(mvcommon, "groq_api_key", lambda: "GROQ-KEY")
    plain = "- The director used real snow for the scene.\n- The lead actor did all stunts.\n"
    monkeypatch.setattr(main, "_groq_chat", lambda *a, **k: plain)

    facts = main.groq_distill_trivia("X", 2000, _snips())
    assert len(facts) == 2
    assert all(f["source"] == "web" for f in facts)
    assert facts[0]["text"] == "The director used real snow for the scene."
    assert facts[1]["text"] == "The lead actor did all stunts."


def test_groq_distill_caps_at_four(monkeypatch):
    """Even when GROQ returns >4 facts, only the first 4 are kept."""
    monkeypatch.setattr(mvcommon, "groq_api_key", lambda: "GROQ-KEY")
    arr = json.dumps([{"text": f"Trivia fact number {i} here.", "source": "IMDb"} for i in range(6)])
    monkeypatch.setattr(main, "_groq_chat", lambda *a, **k: arr)

    facts = main.groq_distill_trivia("X", 2000, _snips())
    assert len(facts) == 4


def test_groq_distill_source_normalized_and_defaulted(monkeypatch):
    """A host-shaped source is normalized; a missing source defaults to 'web'."""
    monkeypatch.setattr(mvcommon, "groq_api_key", lambda: "GROQ-KEY")
    content = json.dumps([
        {"text": "A neat fact about the film here.", "source": "screenrant.com"},  # host -> ScreenRant
        {"text": "A fact with no source attribute at all."},                       # missing -> web
    ])
    monkeypatch.setattr(main, "_groq_chat", lambda *a, **k: content)

    facts = main.groq_distill_trivia("X", 2000, _snips())
    assert facts[0]["source"] == "ScreenRant"
    assert facts[1]["source"] == "web"


def test_groq_distill_no_snippets_returns_empty(monkeypatch):
    """No EXA snippets -> [] without calling GROQ at all."""
    monkeypatch.setattr(mvcommon, "groq_api_key", lambda: "GROQ-KEY")
    monkeypatch.setattr(main, "_groq_chat",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no snippets -> no GROQ call")))
    assert main.groq_distill_trivia("X", 2000, []) == []


def test_groq_distill_no_key_returns_empty(monkeypatch):
    monkeypatch.setattr(mvcommon, "groq_api_key", lambda: "")
    monkeypatch.setattr(main, "_groq_chat",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no key -> no GROQ call")))
    assert main.groq_distill_trivia("X", 2000, _snips()) == []


def test_groq_distill_chat_failure_returns_empty(monkeypatch):
    """A None from _groq_chat (GROQ network/non-200/bad-shape) -> []."""
    monkeypatch.setattr(mvcommon, "groq_api_key", lambda: "GROQ-KEY")
    monkeypatch.setattr(main, "_groq_chat", lambda *a, **k: None)
    assert main.groq_distill_trivia("X", 2000, _snips()) == []


# ===========================================================================
# (d) cache helpers — round-trip, atomic, malformed-degrade, freshness
# ===========================================================================

def test_extra_cache_round_trip(extra_cache):
    """extra_cache_set then extra_cache_get round-trips by str(tmdb_id) — an int and
    a string id resolve to the same entry; the file on disk is keyed by the string."""
    main.extra_cache_set(27205, {"trivia": [{"text": "x", "source": "IMDb"}],
                                 "fetched_at": "2026-01-01T00:00:00+00:00"})
    got = main.extra_cache_get(27205)
    assert got["trivia"] == [{"text": "x", "source": "IMDb"}]
    assert main.extra_cache_get("27205") == got
    raw = json.loads(extra_cache.read_text(encoding="utf-8"))
    assert "27205" in raw


def test_extra_cache_missing_returns_none(extra_cache):
    assert main.extra_cache_get(99999) is None
    assert main.extra_cache_get(None) is None


def test_extra_cache_malformed_degrades(extra_cache):
    """A malformed mvextra.json degrades to {} (never crashes load/get)."""
    extra_cache.write_text("{ not json", encoding="utf-8")
    assert main.extra_cache_load() == {}
    assert main.extra_cache_get(27205) is None


def test_trivia_entry_freshness():
    """_trivia_entry_is_fresh: within TRIVIA_FRESH_DAYS -> True; older -> False;
    missing/unparseable timestamp -> False (re-fetch)."""
    now = mvcommon._now_utc()
    fresh = (now - main.timedelta(days=1)).isoformat()
    stale = (now - main.timedelta(days=main.TRIVIA_FRESH_DAYS + 1)).isoformat()
    assert main._trivia_entry_is_fresh({"fetched_at": fresh}) is True
    assert main._trivia_entry_is_fresh({"fetched_at": stale}) is False
    assert main._trivia_entry_is_fresh({}) is False        # no timestamp -> refetch
    assert main._trivia_entry_is_fresh(None) is False


# ===========================================================================
# (e) cmd_fetch_trivia — write, dedupe, freshness/--force, buckets, no-mutate
# ===========================================================================

def _seed_movie(sandbox, make_video, *, tmdb_id=27205):
    """Seed one movie leaf (Inception). tmdb_id=None omits metadata.tmdb_id (the
    un-enriched case). Returns the id."""
    root = sandbox["local_root"]
    folder = root / "Movies" / "Inception {tmdb-27205}"
    folder.mkdir(parents=True, exist_ok=True)
    make_video(folder / "Inception.2010.mkv", marker=b"M")
    mid = "mov-en-2010-inception"
    meta = {"title": "Inception", "year": 2010}
    if tmdb_id is not None:
        meta["tmdb_id"] = tmdb_id
    movies = {mid: {"status": "local_ready", "uploaded": False, "folder_path": str(folder),
                    "filename": "Inception.2010.mkv", "type": "movie", "metadata": meta}}
    sandbox["lib_movies"].write_text(json.dumps(movies), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")
    return mid


def _seed_show_two_episodes(sandbox, make_video, *, tmdb_id=2316):
    """Seed a season_map + TWO episode leaves of one show, all carrying the SAME show
    tmdb_id (as enrich stamps). Returns the show tmdb_id."""
    root = sandbox["local_root"]
    season_dir = root / "Series" / "The Office {tmdb-2316}" / "Season 01"
    season_dir.mkdir(parents=True, exist_ok=True)
    make_video(season_dir / "The.Office.S01E01.mkv", marker=b"1")
    make_video(season_dir / "The.Office.S01E02.mkv", marker=b"2")
    season_id = "tv-en-2005-the-office-s01"
    ep1 = "tv-en-2005-the-office-s01e01"
    ep2 = "tv-en-2005-the-office-s01e02"
    series = {
        season_id: {"type": "season_map", "folder_path": str(season_dir),
                    "total_episodes": 2, "children": [ep1, ep2]},
        ep1: {"status": "local_ready", "uploaded": False, "folder_path": str(season_dir),
              "filename": "The.Office.S01E01.mkv", "parent_id": season_id,
              "metadata": {"title": "The Office", "year": 2005, "tmdb_id": tmdb_id}},
        ep2: {"status": "local_ready", "uploaded": False, "folder_path": str(season_dir),
              "filename": "The.Office.S01E02.mkv", "parent_id": season_id,
              "metadata": {"title": "The Office", "year": 2005, "tmdb_id": tmdb_id}},
    }
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text(json.dumps(series), encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")
    return tmdb_id


def _patch_pipeline(monkeypatch, facts=None, snippets=None, count=None):
    """Patch exa_search_trivia + groq_distill_trivia at the seam so cmd tests control
    the snippets/facts and (optionally) count calls via the `count` dict."""
    snips = snippets if snippets is not None else [
        {"title": "t", "url": "https://www.imdb.com/x", "text": "..."}]
    out_facts = facts if facts is not None else [
        {"text": "Fact A.", "source": "IMDb"}, {"text": "Fact B.", "source": "ScreenRant"}]

    def fake_exa(title, year):
        if count is not None:
            count["exa"] = count.get("exa", 0) + 1
        return [dict(s) for s in snips]

    def fake_groq(title, year, snippets_arg):
        if count is not None:
            count["groq"] = count.get("groq", 0) + 1
        return [dict(f) for f in out_facts]

    monkeypatch.setattr(main, "exa_search_trivia", fake_exa)
    monkeypatch.setattr(main, "groq_distill_trivia", fake_groq)
    return out_facts


def test_fetch_trivia_writes_cache_keyed_by_tmdb_id(sandbox, make_video, extra_cache, monkeypatch, capsys):
    """cmd_fetch_trivia EXA-searches -> GROQ-distills -> writes mvextra.json keyed by
    tmdb_id, with the contract shape + the per-title progress line."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    _patch_pipeline(monkeypatch)

    main.cmd_fetch_trivia()

    cache = json.loads(extra_cache.read_text(encoding="utf-8"))
    assert "27205" in cache
    entry = cache["27205"]
    assert entry["trivia"] == [{"text": "Fact A.", "source": "IMDb"},
                               {"text": "Fact B.", "source": "ScreenRant"}]
    assert "fetched_at" in entry  # the cache layer stamps it
    out = capsys.readouterr().out
    assert "FETCH TRIVIA" in out
    assert "2 facts [IMDb,ScreenRant]" in out


def test_fetch_trivia_dedupes_episodes_to_one_fetch(sandbox, make_video, extra_cache, monkeypatch, capsys):
    """A show with TWO episodes (same show tmdb_id) triggers exactly ONE EXA+GROQ
    fetch — episodes inherit the show's trivia (dedupe-by-tmdb_id)."""
    _seed_show_two_episodes(sandbox, make_video, tmdb_id=2316)
    count = {}
    _patch_pipeline(monkeypatch, facts=[{"text": "A show fact.", "source": "IMDb"}], count=count)

    main.cmd_fetch_trivia()

    assert count.get("exa") == 1, "two episodes of one show must dedupe to ONE fetch"
    assert count.get("groq") == 1
    cache = json.loads(extra_cache.read_text(encoding="utf-8"))
    assert list(cache.keys()) == ["2316"], f"exactly one cache key (the show id); got {list(cache)}"
    assert "1 distinct title" in capsys.readouterr().out


def test_fetch_trivia_skips_fresh_unless_force(sandbox, make_video, extra_cache, monkeypatch, capsys):
    """A fresh cache entry is skipped (no fetch); --force re-fetches + overwrites."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    count = {}
    _patch_pipeline(monkeypatch, facts=[{"text": "New fact.", "source": "IMDb"}], count=count)

    # Seed a FRESH cache entry for tmdb 27205.
    fresh_iso = mvcommon._now_utc().isoformat()
    main.extra_cache_set(27205, {"trivia": [{"text": "OLD", "source": "web"}], "fetched_at": fresh_iso})

    # 1) No --force: the fresh entry is skipped, the old value survives.
    main.cmd_fetch_trivia()
    assert count.get("exa", 0) == 0, "a fresh entry must be skipped without --force"
    assert main.extra_cache_get(27205)["trivia"] == [{"text": "OLD", "source": "web"}]
    assert "cached (fresh)" in capsys.readouterr().out

    # 2) --force: it IS re-fetched and overwritten.
    main.cmd_fetch_trivia("--force")
    assert count.get("exa") == 1, "--force must re-fetch even a fresh entry"
    assert main.extra_cache_get(27205)["trivia"] == [{"text": "New fact.", "source": "IMDb"}]


def test_fetch_trivia_never_mutates_library(sandbox, make_video, extra_cache, monkeypatch):
    """The headline safety property: fetch_trivia writes ONLY mvextra.json — the three
    library_*.json files are byte-for-byte unchanged."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    _patch_pipeline(monkeypatch)

    lib_paths = [sandbox["lib_movies"], sandbox["lib_series"], sandbox["lib_anime"]]
    before = {p: p.read_bytes() for p in lib_paths}
    before_mtime = {p: p.stat().st_mtime_ns for p in lib_paths}

    main.cmd_fetch_trivia()

    for p in lib_paths:
        assert p.read_bytes() == before[p], f"{p.name} bytes changed — fetch_trivia must not write the library"
        assert p.stat().st_mtime_ns == before_mtime[p], f"{p.name} mtime changed"
    assert main.extra_cache_get(27205) is not None  # but the trivia cache WAS written


def test_fetch_trivia_no_web_results_bucket(sandbox, make_video, extra_cache, monkeypatch, capsys):
    """EXA returning no snippets -> reported 'no web results' (no-results bucket); GROQ
    is never reached and nothing is cached."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    monkeypatch.setattr(main, "exa_search_trivia", lambda t, y: [])
    monkeypatch.setattr(main, "groq_distill_trivia",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no snippets -> no distill")))

    main.cmd_fetch_trivia()

    out = capsys.readouterr().out
    assert "no web results" in out
    assert "no-results=1" in out
    assert main.extra_cache_get(27205) is None


def test_fetch_trivia_distill_empty_is_failed_bucket(sandbox, make_video, extra_cache, monkeypatch, capsys):
    """EXA had material but GROQ produced no fact -> the 'failed' bucket; not cached."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    monkeypatch.setattr(main, "exa_search_trivia", lambda t, y: [{"url": "https://imdb.com/x", "text": "..."}])
    monkeypatch.setattr(main, "groq_distill_trivia", lambda *a, **k: [])

    main.cmd_fetch_trivia()

    out = capsys.readouterr().out
    assert "distill produced no facts" in out
    assert "failed=1" in out
    assert main.extra_cache_get(27205) is None


def test_fetch_trivia_no_tmdb_id_reported(sandbox, make_video, extra_cache, monkeypatch, capsys):
    """A title with NO tmdb_id is counted no-tmdb and never fetched (the cache key is
    the tmdb_id, so an un-enriched title cannot be stored)."""
    _seed_movie(sandbox, make_video, tmdb_id=None)
    monkeypatch.setattr(main, "exa_search_trivia",
                        lambda t, y: (_ for _ in ()).throw(AssertionError("no tmdb -> no fetch")))

    main.cmd_fetch_trivia()

    out = capsys.readouterr().out
    assert "without a tmdb_id" in out
    assert "no-tmdb=1" in out


def test_fetch_trivia_no_exa_key_bails(sandbox, make_video, monkeypatch, capsys):
    """No EXA key configured -> a clear bail, no fetch, no cache write."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "")
    monkeypatch.setattr(mvcommon, "groq_api_key", lambda: "GROQ-KEY")
    main.cmd_fetch_trivia()
    assert "No EXA API key configured" in capsys.readouterr().out


def test_fetch_trivia_no_groq_key_bails(sandbox, make_video, monkeypatch, capsys):
    """No GROQ key configured -> a clear bail (even with an EXA key)."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "EXA-KEY")
    monkeypatch.setattr(mvcommon, "groq_api_key", lambda: "")
    main.cmd_fetch_trivia()
    assert "No GROQ API key configured" in capsys.readouterr().out


def test_fetch_trivia_scope_by_prefix(sandbox, make_video, extra_cache, monkeypatch, capsys):
    """A positional id/prefix restricts the backfill to matching ids only."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    _patch_pipeline(monkeypatch)

    # A prefix that matches nothing -> 0 titles, nothing cached.
    main.cmd_fetch_trivia("tv-does-not-exist")
    assert "0 distinct title" in capsys.readouterr().out
    assert main.extra_cache_get(27205) is None

    # The matching movie prefix -> the movie is fetched.
    main.cmd_fetch_trivia("mov-en-2010-inception")
    assert main.extra_cache_get(27205) is not None
