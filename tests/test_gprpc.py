"""gprpc.py — the read-only Google Photos RPC client (IMP-C25 Step 2b; PLAN Step 2b tests).

Every request is answered by the fake Google Photos' batchexecute emulator (tests/gphotos_fake.py) and
every wait runs on a fake clock (the autouse `clock` fixture): no network, no browser, no real sleeping.
SYNTHETIC data only — every photo id contains "FAKE".

Guards pinned here, each with a disarm (mutation) probe recorded in STATUS.md: the read-only allowlist
(checked before the driver is touched AND in the function that sends), the endpoint-path check that
keeps the XSRF token on photos.google.com, the redacted download-URL wrapper, item_info without URLs,
pacing, retries, the circuit breaker, "not found" narrowed to its status, content-proof hash mapping,
one key per pLFTfd call, paging loops, no file written, and no token / email / URL in any output,
message or locals-capturing traceback.

Note for Steps 7, 12 and 13: pacing and backoff really sleep — a test that drives gprpc through the fake
should install the same fake clock (see `clock` below) or it will wait for real.
"""
import builtins
import hashlib
import io
import json
import logging
import os
import subprocess
import sys
import traceback
import urllib.parse

import pytest
from selenium.common.exceptions import JavascriptException, TimeoutException, WebDriverException

import gphotos_fake
import gprpc
import gpweb
import mainfetch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTOS = gphotos_fake.PHOTOS_URL
READ_ONLY = frozenset({"lcxiM", "EzkLib", "swbisb", "VrseUb", "fDcn4b", "EWgK9e", "pLFTfd"})       # PLAN P12
MUTATING = frozenset({"XwAOJf", "StLnCe", "Pp2Xxe", "ycV3Nd", "OXvT9d", "E1Cajb", "LjmOue", "V8RKJ",  # PLAN P13
                      "Es7fke", "EtUHOe", "DaSgWe", "AQNOFd", "yCLA7", "dnv2s"})
WEB_MARKERS = ("🔹 PROCESSING:", "> Detected Split File", "✅ MOVED:", "✅ ENTRY COMPLETE.")          # PLAN P-13
UNKNOWN_ID = "AF1QipFAKEunknown00000000"
SEARCH = ["x", None, None]  # a harmless EzkLib request


# ================================================================================ harness

class Clock:
    """A fake monotonic clock: gprpc._sleep advances it instantly, so no test ever waits."""

    def __init__(self):
        self.now = 10_000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Every test runs on the fake clock with the LARGEST jitter (deterministic): the pacing gap is
    0.75 s, the backoffs 1.25 / 2.25 / 4.25 s."""
    c = Clock()
    monkeypatch.setattr(gprpc, "_monotonic", c.monotonic)
    monkeypatch.setattr(gprpc, "_sleep", c.sleep)
    monkeypatch.setattr(gprpc, "_uniform", lambda low, high: high)
    return c


class Untouchable:
    """A driver that fails on ANY use — proves that a refusal happens before gprpc touches the driver."""

    def __init__(self):
        object.__setattr__(self, "touched", [])

    def __getattr__(self, name):
        self.touched.append(name)
        raise AssertionError(f"the driver was touched ({name!r}) before the check")

    def __setattr__(self, name, value):
        self.touched.append(name)
        raise AssertionError(f"the driver was touched (set {name!r}) before the check")


def _driver(fake_photos, account="tv"):
    """A fresh browser session on about:blank — gprpc has to open Google Photos by itself."""
    return fake_photos.driver(account)


def _answer_with(driver, answers):
    """The next execute_async_script calls return (or raise) these answers, then behave normally again.
    Injected answers never reach the fake's rpc_log."""
    real, queue = driver.execute_async_script, list(answers)

    def execute_async_script(script, *args):
        if queue:
            answer = queue.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        return real(script, *args)

    driver.execute_async_script = execute_async_script


def _record_of(item):
    """The record gprpc must return for a fake item — computed from the fake's own facts."""
    return {"photo_id": item.photo_id, "width": item.width, "height": item.height, "taken_ms": item.taken_ms,
            "dedup_key": item.dedup_key, "tz_offset_ms": item.tz_offset_ms, "upload_ms": item.upload_ms,
            "partial_upload": item.partial_upload, "archived": item.archived, "duration_ms": item.duration_ms}


def _minted_urls(fake_photos):
    """Every download URL the fake has minted so far (the secrets the leak tests look for)."""
    return [f"https://{gphotos_fake.DOWNLOAD_HOST}/{m['kind']}/{token}" for token, m in fake_photos.minted.items()]


def _sha1(data):
    return hashlib.sha1(data).hexdigest()


def _formatted(exc):
    """str(exc) + its traceback formatted WITH every frame's locals (what a locals-capturing logger shows)."""
    return str(exc) + "\n" + "".join(traceback.TracebackException.from_exception(exc, capture_locals=True).format())


def _capture(fn, *args, **kwargs):
    """fn(*args) -> (exception or None, texts). The formatting happens in THIS frame, whose locals hold
    nothing but the call itself — so the texts show exactly what gprpc's own frames expose."""
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        return exc, [_formatted(exc)]
    return None, []


def _raise_with(link):
    raise RuntimeError("the download did not start")  # `link` is a local of the raising frame


# ================================================================================ the allowlist (P-9, P12, P13)

def test_the_allowlist_is_exactly_the_read_only_p12_ids():
    assert gprpc.ALLOWED_RPCIDS == READ_ONLY and isinstance(gprpc.ALLOWED_RPCIDS, frozenset)
    assert gprpc.ALLOWED_RPCIDS.isdisjoint(MUTATING)
    assert set(gphotos_fake.READ_ONLY_RPCIDS) == READ_ONLY and gphotos_fake.MUTATING_RPCIDS == MUTATING
    assert set(gpweb.PARSERS) == READ_ONLY  # every allowed id has its typed parser


@pytest.mark.parametrize("rpcid", sorted(MUTATING) + ["zzzzzz", "", "lcxim", " lcxiM", "EzkLib\n", "lcxiM,XwAOJf",
                                                      None, 42, ["lcxiM"]], ids=repr)
def test_every_mutating_or_unknown_rpcid_is_refused_before_the_driver_is_touched(fake_photos, rpcid):
    untouchable = Untouchable()
    with pytest.raises(gprpc.RpcRefused):
        gprpc.call(untouchable, rpcid, [["AF1QipFAKEanything00"], None])
    assert untouchable.touched == []
    d = _driver(fake_photos)
    with pytest.raises(gprpc.RpcRefused, match="nothing was sent"):
        gprpc.call(d, rpcid, [["AF1QipFAKEanything00"], None])
    assert fake_photos.rpc_log == [] and d.current_url == "about:blank" and d.script_log == []


def test_the_function_that_sends_repeats_the_allowlist_check(fake_photos):
    """Defense in depth (P-9): code that got around call() still cannot make _exchange send a mutating id."""
    d = _driver(fake_photos)
    for rpcid in sorted(MUTATING):
        with pytest.raises(gprpc.RpcRefused):
            gprpc._exchange(d, gprpc._session(d), rpcid, "[]")
    assert fake_photos.rpc_log == [] and d.current_url == "about:blank"


def test_a_request_that_is_not_json_is_refused_before_the_driver_is_touched():
    untouchable = Untouchable()
    with pytest.raises(TypeError):
        gprpc.call(untouchable, "EzkLib", [{1, 2}, None, None])
    assert untouchable.touched == []


def test_the_error_family_consumers_rely_on():
    """`except gprpc.RpcUnavailable` — the fallback trigger PLAN Steps 7, 12 and 13 name — catches every
    "no usable RPC answer" case; a refusal is never one of them."""
    assert issubclass(gprpc.RpcShapeError, gprpc.RpcUnavailable)
    assert issubclass(gprpc.RpcServerError, gprpc.RpcUnavailable)
    assert not issubclass(gprpc.RpcRefused, gprpc.RpcUnavailable)  # a refusal is a bug, never a UI fallback
    not_found = gprpc.RpcServerError("VrseUb", gprpc.STATUS_NOT_FOUND)
    assert not_found.not_found and not_found.rpcid == "VrseUb" and "VrseUb" in str(not_found)
    assert not gprpc.RpcServerError("pLFTfd", 7).not_found and not gprpc.RpcServerError("pLFTfd").not_found


# ================================================================================ the P11 request

def test_call_sends_the_p11_request_from_the_signed_in_photos_tab(fake_photos):
    item = fake_photos.add_item("tv", "Sample.Show.S01E01 [a1a1a1].mkv")
    d = _driver(fake_photos)
    page = gprpc.call(d, "EzkLib", ["a1a1a1", None, None])
    assert [r["photo_id"] for r in page["items"]] == [item.photo_id]
    assert d.current_url == PHOTOS  # gprpc opened Google Photos itself (the tab was on about:blank)
    (entry,) = fake_photos.rpc_log
    wiz = fake_photos.accounts["tv"].wiz
    assert entry["path"] == wiz["eptZe"] + "data/batchexecute" and entry["at_ok"] is True
    assert entry["params"] == {"rpcids": "EzkLib", "source-path": "/", "f.sid": wiz["FdrFJe"], "bl": wiz["cfb2h"],
                               "pageId": "none", "rt": "c"}
    assert entry["rpcid_freq"] == "EzkLib" and entry["request"] == ["a1a1a1", None, None]
    assert "method: 'POST'" in gprpc._FETCH_JS and "credentials: 'include'" in gprpc._FETCH_JS
    assert "application/x-www-form-urlencoded;charset=UTF-8" in gprpc._FETCH_JS
    assert "oPEP7c" not in gprpc._WIZ_JS  # a call never reads the account email


def test_call_stays_on_the_current_photos_page_and_names_it_as_the_source_path(fake_photos):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    d = _driver(fake_photos)
    d.get(f"{PHOTOS}/photo/{item.photo_id}")
    gprpc.call(d, "VrseUb", [item.photo_id, None, None, None, None])
    assert d.current_url == f"{PHOTOS}/photo/{item.photo_id}"  # no navigation away from the item page
    (entry,) = fake_photos.rpc_log
    assert entry["params"]["source-path"] == f"/photo/{item.photo_id}"


@pytest.mark.parametrize("query", ["Sample தமிழ்  Movie [b2b2b2]", "A+B & C=D %20 #1 ?x", 'quote " and \\ back'])
def test_a_query_reaches_google_photos_character_for_character(fake_photos, query):
    d = _driver(fake_photos)
    gprpc.call(d, "EzkLib", [query, None, None])
    assert fake_photos.rpc_log[-1]["request"] == [query, None, None]


def test_missing_page_tokens_raise_rpc_unavailable_and_nothing_is_sent(fake_photos):
    fake_photos.wiz_token_missing["tv"] = True
    d = _driver(fake_photos)
    with pytest.raises(gprpc.RpcUnavailable, match="page tokens"):
        gprpc.call(d, "EzkLib", SEARCH)
    assert fake_photos.rpc_log == [] and gprpc._FETCH_JS[:80] not in d.script_log  # no request left the page
    assert gprpc.stats(d) == {"calls": 1, "errors": 1, "breaker_open": False}


@pytest.mark.parametrize("endpoint", ["@evil.example/", ".evil.example/", "//evil.example/", "https://evil.example/",
                                      "/_/PhotosUi", "_/PhotosUi/", "/x?y=/", "/x#/", "/x y/", ""])
def test_the_xsrf_token_only_ever_goes_to_photos_google_com(fake_photos, endpoint):
    fake_photos.accounts["tv"].wiz["eptZe"] = endpoint
    d = _driver(fake_photos)
    with pytest.raises(gprpc.RpcUnavailable, match="page tokens"):
        gprpc.call(d, "EzkLib", SEARCH)
    assert fake_photos.rpc_log == [] and gprpc._FETCH_JS[:80] not in d.script_log  # no request left the page


@pytest.mark.parametrize("state", ["signed_out", "expired"])
def test_a_signed_out_profile_raises_session_expired_and_nothing_is_sent(fake_photos, state):
    item = fake_photos.add_item("anime", "Sample Anime - 01 [a1a1a1].mkv")
    fake_photos.session["anime"] = state
    d = _driver(fake_photos, "anime")
    for fn, args in ((gprpc.call, ("EzkLib", SEARCH)), (gprpc.item_info, (item.photo_id,)),
                     (gprpc.download_url, (item.photo_id,)), (gprpc.hash_lookup, ([item.sha1_hex],)),
                     (gprpc.signed_in_account, ())):
        with pytest.raises(mainfetch.SessionExpiredError):
            fn(d, *args)
    assert fake_photos.rpc_log == []


def test_an_rpc_only_session_counts_as_signed_in_so_a_later_bounce_is_a_throttle(fake_photos):
    # IMP-C25 thfix: every request runs mainfetch.check_session_alive — on Google Photos it notes the session as
    # signed in, so when Google later sends the tab to its signed-out page (the R3 dry runs) the next look reads a
    # throttle, never a sign-out; an "expired" session (a Google sign-in page) still reads signed out.
    fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    d = _driver(fake_photos)
    d.get(gphotos_fake.PHOTOS_URL)                       # on Google Photos already, as init_driver leaves the tab
    gprpc.call(d, "EzkLib", SEARCH)                                    # RPC only: no page check of its own
    assert getattr(d, "_mv_photos_seen", False) is True
    fake_photos.session["tv"] = "signed_out"
    d.get(gphotos_fake.PHOTOS_URL)                                     # Google bounces the tab
    with pytest.raises(mainfetch.PhotosThrottledError) as err:
        gprpc.call(d, "EzkLib", SEARCH)
    assert err.value.page == "about"
    fake_photos.session["tv"] = "expired"
    with pytest.raises(mainfetch.SessionExpiredError) as err:
        gprpc.call(d, "EzkLib", SEARCH)
    assert type(err.value) is mainfetch.SessionExpiredError


def test_the_photos_url_is_mainfetchs():
    assert gprpc._PHOTOS_URL == mainfetch.PHOTOS_URL and gprpc._PHOTOS_HOST == "photos.google.com"


def test_gprpc_imports_no_selenium_and_no_mediavault_module_at_import_time():
    code = ("import sys, gprpc; print(sorted(m for m in ('selenium', 'mainfetch', 'mvcommon', 'gpcapture', 'main') "
            "if m in sys.modules))")
    run = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "[]"


# ================================================================================ pacing, retries, circuit breaker

def test_pacing_keeps_the_minimum_interval_plus_jitter_between_requests(fake_photos, clock, monkeypatch):
    sent_at, real = [], fake_photos._batchexecute

    def batchexecute(account, url, form_body):
        sent_at.append(clock.now)
        return real(account, url, form_body)

    monkeypatch.setattr(fake_photos, "_batchexecute", batchexecute)
    d = _driver(fake_photos)
    for _ in range(3):
        gprpc.call(d, "EzkLib", SEARCH)
    gap = gprpc.RPC_MIN_INTERVAL_S + gprpc.RPC_JITTER_S  # the fixture always draws the largest jitter
    assert clock.sleeps == [gap, gap]
    assert [round(b - a, 6) for a, b in zip(sent_at, sent_at[1:])] == [gap, gap]
    clock.now += 10  # the caller spent time elsewhere …
    gprpc.call(d, "EzkLib", SEARCH)
    assert clock.sleeps == [gap, gap]  # … so no extra wait
    monkeypatch.setattr(gprpc, "_uniform", lambda low, high: low)
    gprpc.call(d, "EzkLib", SEARCH)
    assert clock.sleeps[-1] == gprpc.RPC_MIN_INTERVAL_S and len(sent_at) == 5  # jitter 0: still the minimum


@pytest.mark.parametrize("kind", ["http429", "http500", "no_envelope"])
def test_a_retryable_failure_is_retried_after_a_backoff(fake_photos, clock, kind):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    fake_photos.fail_next_rpc(kind)
    d = _driver(fake_photos)
    assert [r["photo_id"] for r in gprpc.call(d, "EzkLib", ["a1a1a1", None, None])["items"]] == [item.photo_id]
    assert len(fake_photos.rpc_log) == 2 and clock.sleeps == [gprpc.RPC_BACKOFF_S[0] + gprpc.RPC_JITTER_S]
    assert gprpc.stats(d) == {"calls": 2, "errors": 1, "breaker_open": False}


def test_three_retries_with_a_growing_backoff_then_the_answer(fake_photos, clock):
    for kind in ("http429", "http500", "no_envelope"):
        fake_photos.fail_next_rpc(kind)
    d = _driver(fake_photos)
    gprpc.call(d, "EzkLib", SEARCH)
    jitter = gprpc.RPC_JITTER_S
    assert clock.sleeps == [1.0 + jitter, 2.0 + jitter, 4.0 + jitter] and len(fake_photos.rpc_log) == 4
    assert gprpc.stats(d) == {"calls": 4, "errors": 3, "breaker_open": False}


def test_page_context_faults_are_retried(fake_photos):
    d = _driver(fake_photos)
    real_get, real_script = d.get, d.execute_script
    get_faults, script_faults = [WebDriverException("net::ERR_NETWORK_CHANGED")], [JavascriptException("reload")]

    def get(url):
        if get_faults:
            raise get_faults.pop()
        return real_get(url)

    def execute_script(script, *args):
        if script_faults and "WIZ_global_data" in script:
            raise script_faults.pop()
        return real_script(script, *args)

    d.get, d.execute_script = get, execute_script
    _answer_with(d, [TimeoutException("script timeout")])
    assert gprpc.call(d, "EzkLib", SEARCH)["items"] == []  # navigation, token read, script timeout — then the answer
    assert gprpc.stats(d) == {"calls": 4, "errors": 3, "breaker_open": False}
    _answer_with(d, [{"status": 0, "text": "TypeError: Failed to fetch"}, None, {"text": "no status"}])
    assert gprpc.call(d, "EzkLib", SEARCH)["items"] == []
    assert gprpc.stats(d) == {"calls": 8, "errors": 6, "breaker_open": False}
    assert len(fake_photos.rpc_log) == 2  # only the answered attempts reached Google Photos


def test_exhausted_retries_raise_rpc_unavailable(fake_photos):
    fake_photos.fail_next_rpc("http500", count=4)
    d = _driver(fake_photos)
    with pytest.raises(gprpc.RpcUnavailable, match="no answer after 4 attempts"):
        gprpc.call(d, "EzkLib", SEARCH)
    assert len(fake_photos.rpc_log) == 4 and gprpc.stats(d) == {"calls": 4, "errors": 4, "breaker_open": False}


def test_five_consecutive_failures_turn_rpc_off_for_the_rest_of_the_session(fake_photos, clock):
    fake_photos.fail_next_rpc("http500", count=5)
    d = _driver(fake_photos)
    with pytest.raises(gprpc.RpcUnavailable, match="no answer after 4 attempts"):
        gprpc.call(d, "EzkLib", SEARCH)
    with pytest.raises(gprpc.RpcUnavailable, match="consecutive failures"):
        gprpc.call(d, "EzkLib", SEARCH)  # its first attempt is the 5th failure in a row
    assert len(fake_photos.rpc_log) == 5 and gprpc.stats(d) == {"calls": 5, "errors": 5, "breaker_open": True}
    sleeps, url = list(clock.sleeps), d.current_url
    for fn, args in ((gprpc.call, ("lcxiM", [None, None, 500, None, 1, 3])), (gprpc.item_info, (UNKNOWN_ID,)),
                     (gprpc.hash_lookup, (["00" * 20],)), (gprpc.download_url, (UNKNOWN_ID,)),
                     (gprpc.search, ("x",))):
        with pytest.raises(gprpc.RpcUnavailable, match="consecutive failures"):
            fn(d, *args)
    assert len(fake_photos.rpc_log) == 5 and clock.sleeps == sleeps and d.current_url == url  # nothing sent / waited
    assert gprpc.stats(d)["calls"] == 5
    assert gprpc.call(_driver(fake_photos), "EzkLib", SEARCH)["items"] == []  # the breaker is per browser session


def test_an_answer_resets_the_consecutive_failure_count(fake_photos):
    d = _driver(fake_photos)
    for _ in range(2):
        fake_photos.fail_next_rpc("http500", count=4)
        with pytest.raises(gprpc.RpcUnavailable, match="no answer after 4 attempts"):
            gprpc.call(d, "EzkLib", SEARCH)
        gprpc.call(d, "EzkLib", SEARCH)  # an answer: the count starts again
    assert gprpc.stats(d) == {"calls": 10, "errors": 8, "breaker_open": False}


@pytest.mark.parametrize("kind, rpcid, error", [("changed_shape", "EzkLib", gprpc.RpcShapeError),
                                                ("changed_items", "lcxiM", gprpc.RpcShapeError),
                                                ("changed_items", "VrseUb", gprpc.RpcShapeError),
                                                ("changed_items", "fDcn4b", gprpc.RpcShapeError),
                                                ("empty_payload", "EzkLib", gprpc.RpcServerError)])
def test_a_changed_shape_or_an_error_status_fails_closed_without_a_retry(fake_photos, clock, kind, rpcid, error):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    request = {"EzkLib": SEARCH, "lcxiM": [None, None, 500, None, 1, 3],
               "VrseUb": [item.photo_id, None, None, None, None], "fDcn4b": [item.photo_id, 1, None, None, 1]}[rpcid]
    fake_photos.fail_next_rpc(kind)
    d = _driver(fake_photos)
    with pytest.raises(error) as raised:
        gprpc.call(d, rpcid, request)
    assert type(raised.value) is error
    assert len(fake_photos.rpc_log) == 1 and clock.sleeps == []
    assert gprpc.stats(d) == {"calls": 1, "errors": 1, "breaker_open": False}


@pytest.mark.parametrize("answer, error, message", [
    ({"status": 400, "text": "Bad Request"}, gprpc.RpcUnavailable, "HTTP 400"),
    ({"status": 403, "text": ""}, gprpc.RpcUnavailable, "HTTP 403"),
    ({"status": 200, "text": ")]}'\n\n" + json.dumps([["wrb.fr", "EzkLib", "{not json", None, None, None, "generic"]])},
     gprpc.RpcShapeError, "not JSON")])
def test_a_non_retryable_answer_is_not_retried(fake_photos, clock, answer, error, message):
    d = _driver(fake_photos)
    _answer_with(d, [answer])
    with pytest.raises(error, match=message) as raised:
        gprpc.call(d, "EzkLib", SEARCH)
    assert type(raised.value) is error
    assert clock.sleeps == [] and gprpc.stats(d) == {"calls": 1, "errors": 1, "breaker_open": False}


def test_hard_failures_count_toward_the_circuit_breaker(fake_photos):
    fake_photos.fail_next_rpc("changed_shape", count=5)
    d = _driver(fake_photos)
    for _ in range(5):
        with pytest.raises(gprpc.RpcShapeError):
            gprpc.call(d, "EzkLib", SEARCH)
    with pytest.raises(gprpc.RpcUnavailable, match="consecutive failures"):
        gprpc.call(d, "EzkLib", SEARCH)
    assert len(fake_photos.rpc_log) == 5


def test_not_found_is_an_answer_not_a_failure(fake_photos):
    gone = fake_photos.add_item("tv", "Gone [c3c3c3].mkv", deleted=True)
    d = _driver(fake_photos)
    fake_photos.fail_next_rpc("http500", count=4)
    with pytest.raises(gprpc.RpcUnavailable, match="no answer after 4 attempts"):
        gprpc.call(d, "EzkLib", SEARCH)  # 4 failures in a row …
    assert gprpc.item_info(d, gone.photo_id) is None  # … then a stale photo_id: "not found" is an answer
    assert gprpc.item_info(d, UNKNOWN_ID) is None
    with pytest.raises(gprpc.RpcServerError) as raised:
        gprpc.call(d, "VrseUb", [UNKNOWN_ID, None, None, None, None])
    assert raised.value.not_found and raised.value.code == gprpc.STATUS_NOT_FOUND
    fake_photos.fail_next_rpc("http500", count=4)
    with pytest.raises(gprpc.RpcUnavailable, match="no answer after"):  # 4 more — still below the breaker
        gprpc.call(d, "EzkLib", SEARCH)
    assert gprpc.stats(d) == {"calls": 11, "errors": 8, "breaker_open": False}


def test_only_the_not_found_status_means_no_such_item(fake_photos, monkeypatch):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    monkeypatch.setattr(fake_photos, "_h_VrseUb", lambda acc, req: (None, 7))  # e.g. permission denied
    d = _driver(fake_photos)
    for fn in (gprpc.item_info, gprpc.download_url):
        with pytest.raises(gprpc.RpcServerError) as raised:
            fn(d, item.photo_id)
        assert raised.value.code == 7 and not raised.value.not_found


# ================================================================================ hash_lookup (swbisb)

def test_hash_lookup_maps_every_answer_back_to_its_digest(fake_photos):
    a = fake_photos.add_item("tv", "A [a1a1a1].mkv", data=b"FAKE-CONTENT-A", partial_upload=True)
    b = fake_photos.add_item("tv", "B [b2b2b2].chunk.001.mkv", data=b"FAKE-CONTENT-B", thumb_shape="long")
    elsewhere = fake_photos.add_item("movies", "C [c3c3c3].mkv", data=b"FAKE-CONTENT-C")
    missing = _sha1(b"FAKE-NEVER-UPLOADED")
    d = _driver(fake_photos)
    got = gprpc.hash_lookup(d, [a.sha1_hex, b.sha1_hex.upper(), a.sha1_hex, missing, f" {elsewhere.sha1_hex} "])
    assert got == {a.sha1_hex: [_record_of(a)], b.sha1_hex: [_record_of(b)], missing: [],
                   elsewhere.sha1_hex: []}  # C lives in another account
    assert list(got) == [a.sha1_hex, b.sha1_hex, missing, elsewhere.sha1_hex]
    (entry,) = fake_photos.rpc_log
    assert entry["request"] == [[a.hash_b64, b.hash_b64, gpweb.sha1_hex_to_std_b64(missing), elsewhere.hash_b64],
                                None, 3, 0]
    assert all(len(h) == 28 and h.endswith("=") for h in entry["request"][0])  # STANDARD base64, padded (P12)
    assert gprpc.hash_lookup(d, []) == {} and len(fake_photos.rpc_log) == 1  # nothing to ask: no request


def test_hash_lookup_asks_at_most_fifty_digests_per_request(fake_photos):
    target = fake_photos.add_item("tv", "T [t1t1t1].mkv", data=b"FAKE-TARGET")
    digests = [_sha1(f"FAKE-{i}".encode()) for i in range(119)] + [target.sha1_hex]
    d = _driver(fake_photos)
    got = gprpc.hash_lookup(d, digests)
    assert [len(e["request"][0]) for e in fake_photos.rpc_log] == [50, 50, 20]
    assert len(got) == 120 and got[target.sha1_hex] == [_record_of(target)]
    assert sum(len(found) for found in got.values()) == 1


def test_hash_lookup_keeps_only_items_whose_own_dedup_key_is_the_digest(fake_photos, monkeypatch):
    a = fake_photos.add_item("tv", "A [a1a1a1].mkv", data=b"FAKE-CONTENT-A")
    b = fake_photos.add_item("tv", "B [b2b2b2].mkv", data=b"FAKE-CONTENT-B")
    other = fake_photos.add_item("tv", "Other [o1o1o1].mkv", data=b"FAKE-OTHER")
    monkeypatch.setattr(fake_photos, "_h_swbisb", lambda acc, req: (  # A's hash paired with ANOTHER item
        [[[a.hash_b64, gphotos_fake.item_array(other)], [b.hash_b64, gphotos_fake.item_array(b)]]], None))
    d = _driver(fake_photos)
    assert gprpc.hash_lookup(d, [a.sha1_hex, b.sha1_hex]) == {a.sha1_hex: [], b.sha1_hex: [_record_of(b)]}


@pytest.mark.parametrize("bad", [["xyz"], ["85df66012c86f692618ac3d7152dd9afb32aaf6"], ["g" * 40], [None],
                                 [b"\x00" * 20], "85df66012c86f692618ac3d7152dd9afb32aaf6f"], ids=repr)
def test_hash_lookup_refuses_a_malformed_digest_before_sending(fake_photos, bad):
    d = _driver(fake_photos)
    with pytest.raises(ValueError):
        gprpc.hash_lookup(d, bad)
    assert fake_photos.rpc_log == [] and d.current_url == "about:blank"


# ================================================================================ item_info (VrseUb + fDcn4b)

def test_item_info_merges_both_answers_and_carries_no_url(fake_photos, clock):
    item = fake_photos.add_item("movies", "Sample Movie (2019) [b2b2b2].mkv", width=3840, height=2160,
                                archived=True, tz_offset_min=-330)
    d = _driver(fake_photos, "movies")
    info = gprpc.item_info(d, item.photo_id)
    assert info == {**_record_of(item), "filename": item.filename, "size_bytes": item.size_bytes}
    assert fake_photos.rpc_ids_sent() == ["VrseUb", "fDcn4b"]
    assert clock.sleeps == [gprpc.RPC_MIN_INTERVAL_S + gprpc.RPC_JITTER_S]  # the second request was paced
    text = json.dumps(info)
    assert "http" not in text and gphotos_fake.DOWNLOAD_HOST not in text and gphotos_fake.THUMB_HOST not in text
    assert _minted_urls(fake_photos)  # the answer DID carry a fresh link — item_info dropped it


def test_item_info_is_none_for_an_item_this_account_does_not_hold(fake_photos):
    gone = fake_photos.add_item("tv", "Gone [c3c3c3].mkv", deleted=True)
    in_movies = fake_photos.add_item("movies", "Sample.Show.S02E01 [x1x1x1].mkv")  # the wrong-account case (P-4)
    d = _driver(fake_photos, "tv")
    assert gprpc.item_info(d, gone.photo_id) is None
    assert gprpc.item_info(d, UNKNOWN_ID) is None
    assert gprpc.item_info(d, in_movies.photo_id) is None
    assert fake_photos.rpc_ids_sent() == ["VrseUb"] * 3  # no item-info-ext request for a missing item


def test_item_info_keeps_the_record_when_the_ext_call_answers_an_error_status(fake_photos, monkeypatch):
    item = fake_photos.add_item("tv", "Shared [s1s1s1].mkv")
    monkeypatch.setattr(fake_photos, "_h_fDcn4b", lambda acc, req: (None, 7))  # e.g. not in the own library
    d = _driver(fake_photos)
    assert gprpc.item_info(d, item.photo_id) == {**_record_of(item), "filename": None, "size_bytes": None}


def test_item_info_fails_closed_when_the_answers_contradict_each_other(fake_photos, monkeypatch):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv", data=b"FAKE-A")
    other = fake_photos.add_item("tv", "Other [o1o1o1].mkv", data=b"FAKE-O")
    d = _driver(fake_photos)
    wrong_key = gphotos_fake.ext_array(item)
    wrong_key[11] = other.dedup_key
    for ext in (gphotos_fake.ext_array(other), wrong_key):  # another item · the same item, other bytes
        monkeypatch.setattr(fake_photos, "_h_fDcn4b", lambda acc, req, ext=ext: ([ext], None))
        with pytest.raises(gprpc.RpcShapeError, match="disagree"):
            gprpc.item_info(d, item.photo_id)
    monkeypatch.setattr(fake_photos, "_h_VrseUb", lambda acc, req: (fake_photos._vrseub_payload(acc, other), None))
    for fn in (gprpc.item_info, gprpc.download_url):
        with pytest.raises(gprpc.RpcShapeError, match="another item"):
            fn(d, item.photo_id)
    assert gprpc.stats(d)["errors"] == 4  # contradictions feed the circuit breaker


@pytest.mark.parametrize("bad", ["", "short", "AF1Qip/../x", "AF1QipFAKE0000000\n", None, 12345678901], ids=repr)
def test_the_helpers_refuse_a_malformed_photo_id_before_sending(fake_photos, bad):
    d = _driver(fake_photos)
    for fn in (gprpc.item_info, gprpc.download_url, lambda drv, pid: gprpc.batch_info(drv, [pid])):
        with pytest.raises(ValueError):
            fn(d, bad)
    assert fake_photos.rpc_log == [] and d.current_url == "about:blank"


# ================================================================================ batch_info (EWgK9e)

def test_batch_info_maps_every_requested_id_back(fake_photos):
    good = fake_photos.add_item("movies", "A [a1a1a1].chunk.001.mkv")
    saver = fake_photos.add_item("movies", "B [b1b1b1].mkv", original_quality=False)
    d = _driver(fake_photos, "movies")
    got = gprpc.batch_info(d, [saver.photo_id, UNKNOWN_ID, good.photo_id, saver.photo_id])
    assert list(got) == [saver.photo_id, UNKNOWN_ID, good.photo_id] and got[UNKNOWN_ID] is None
    for item in (good, saver):
        assert got[item.photo_id] == {"photo_id": item.photo_id, "filename": item.filename,
                                      "size_bytes": item.size_bytes, "taken_ms": item.taken_ms,
                                      "tz_offset_ms": item.tz_offset_ms, "upload_ms": item.upload_ms,
                                      "original_quality": item.original_quality}
    mask = [None] * 24 + [[]] + [None] * 11 + [[]]
    (entry,) = fake_photos.rpc_log
    assert entry["request"] == [[[[[saver.photo_id], [UNKNOWN_ID], [good.photo_id]]], [mask]]]  # PLAN P12


def test_batch_info_asks_at_most_fifty_ids_per_request(fake_photos):
    items = [fake_photos.add_item("tv", f"S [{i:06d}].mkv") for i in range(120)]
    d = _driver(fake_photos)
    got = gprpc.batch_info(d, [it.photo_id for it in items])
    assert [len(e["request"][0][0][0]) for e in fake_photos.rpc_log] == [50, 50, 20]
    assert all(got[it.photo_id]["filename"] == it.filename for it in items)


# ================================================================================ timeline_at (lcxiM)

def test_timeline_at_starts_at_the_taken_second(fake_photos):
    older = fake_photos.add_item("tv", "Older [o1o1o1].mkv", taken="2022-02-05T11:49:40Z")
    chunk1 = fake_photos.add_item("tv", "Set [s1s1s1].chunk.001.mkv", taken="2022-02-05T11:49:46Z")
    chunk2 = fake_photos.add_item("tv", "Set [s1s1s1].chunk.002.mkv", taken="2022-02-05T11:49:46Z")
    fake_photos.add_item("tv", "Newer [n1n1n1].mkv", taken="2022-02-05T11:50:00Z")
    d = _driver(fake_photos)
    page = gprpc.timeline_at(d, chunk1.taken_ms)
    assert {r["photo_id"] for r in page["items"][:2]} == {chunk1.photo_id, chunk2.photo_id}  # one second (F4)
    assert all(r["taken_ms"] == chunk1.taken_ms for r in page["items"][:2])
    assert page["items"][2] == _record_of(older) and len(page["items"]) == 3
    assert (page["next_page_id"], page["last_timestamp_ms"], page["rejected"]) == (None, older.taken_ms, 0)
    assert fake_photos.rpc_log[0]["request"] == [None, chunk1.taken_ms, 500, None, 1, 3]


def test_timeline_at_pages_the_whole_account_newest_first_archive_included(fake_photos):
    items = [fake_photos.add_item("anime", f"Sample Anime - {i:02d} [a{i:05d}].mkv", taken=f"2024-05-0{i}T00:00:00Z",
                                  archived=(i == 3)) for i in range(1, 6)]
    d = _driver(fake_photos, "anime")
    seen, page_id = [], None
    while True:
        page = gprpc.timeline_at(d, None, page_size=2, page_id=page_id)
        seen += [r["photo_id"] for r in page["items"]]
        page_id = page["next_page_id"]
        if page_id is None:
            break
    assert seen == [it.photo_id for it in reversed(items)] and len(fake_photos.rpc_log) == 3


@pytest.mark.parametrize("kwargs", [{"taken_ms": "1644061786000"}, {"taken_ms": 1.5}, {"taken_ms": True},
                                    {"page_size": 0}, {"page_size": 501}, {"page_size": True}, {"page_id": ""},
                                    {"page_id": 3}], ids=repr)
def test_timeline_at_checks_its_arguments_before_sending(fake_photos, kwargs):
    d = _driver(fake_photos)
    with pytest.raises(ValueError):
        gprpc.timeline_at(d, **{"taken_ms": None, **kwargs})
    assert fake_photos.rpc_log == []


# ================================================================================ search / recent_uploads (EzkLib)

def test_search_pages_until_the_results_end_or_max_pages(fake_photos):
    items = [fake_photos.add_item("tv", f"Sample.Show.S01E0{i} [q1q1q1].mkv", taken=f"2022-02-0{i}T00:00:00Z")
             for i in range(1, 6)]
    fake_photos.ezklib_page_size = 2
    d = _driver(fake_photos)
    newest_first = [it.photo_id for it in reversed(items)]
    got = gprpc.search(d, "q1q1q1")
    assert [r["photo_id"] for r in got["items"]] == newest_first and got["items"][0] == _record_of(items[-1])
    assert (got["pages"], got["truncated"], got["rejected"]) == (3, False, 0)
    assert [e["request"] for e in fake_photos.rpc_log] == [["q1q1q1", None, None], ["q1q1q1", None, "FAKEP:EzkLib:2"],
                                                          ["q1q1q1", None, "FAKEP:EzkLib:4"]]
    short = gprpc.search(d, "q1q1q1", max_pages=2)
    assert [r["photo_id"] for r in short["items"]] == newest_first[:4] and short["truncated"] is True
    assert gprpc.search(d, "no such thing") == {"items": [], "pages": 1, "truncated": False, "rejected": 0}
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            gprpc.search(d, bad)
    with pytest.raises(ValueError):
        gprpc.search(d, "q1q1q1", max_pages=0)


def test_a_paging_loop_is_never_followed(fake_photos, monkeypatch):
    item = fake_photos.add_item("tv", "Loop [l1l1l1].mkv")
    monkeypatch.setattr(fake_photos, "_h_EzkLib",
                        lambda acc, req: ([[gphotos_fake.item_array(item)], "FAKEP:loop"], None))
    d = _driver(fake_photos)
    for fn, args in ((gprpc.search, ("l1l1l1",)), (gprpc.recent_uploads, (set(),))):
        with pytest.raises(gprpc.RpcShapeError, match="paging loop"):
            fn(d, *args, max_pages=10)
    assert len(fake_photos.rpc_log) == 4  # each: page 1, the looping page id once — never a third request


def test_recent_uploads_pages_until_the_first_known_id(fake_photos):
    items = [fake_photos.add_item("tv", f"New {i} [r{i}r{i}r{i}].mkv", upload_ms=1_790_000_000_000 + i * 60_000)
             for i in range(1, 6)]  # uploaded in the order 1 … 5 — 5 is the newest
    ids = [it.photo_id for it in items]
    fake_photos.ezklib_page_size = 2
    d = _driver(fake_photos)
    got = gprpc.recent_uploads(d, {ids[1]})  # item 2 is already in the inventory
    assert [r["photo_id"] for r in got["items"]] == [ids[4], ids[3], ids[2]]
    assert (got["pages"], got["complete"], got["rejected"]) == (2, True, 0)
    assert got["items"][0] == _record_of(items[4])
    assert fake_photos.rpc_log[0]["request"] == ["", [[4, "ra", 0, 0]], None]
    first_run = gprpc.recent_uploads(d, [])
    assert [r["photo_id"] for r in first_run["items"]] == ids[::-1] and first_run["complete"] is True
    capped = gprpc.recent_uploads(d, [], max_pages=1)
    assert [r["photo_id"] for r in capped["items"]] == [ids[4], ids[3]] and capped["complete"] is False
    with pytest.raises(TypeError):
        gprpc.recent_uploads(d, ids[0])  # one id as a bare string is a caller bug, not a set of characters


# ================================================================================ download_url (P-20)

def test_download_url_mints_the_item_info_link_and_it_downloads_the_original(fake_photos, tmp_path):
    item = fake_photos.add_item("tv", "Sample.Show.S01E01 [a1a1a1].mkv", data=b"FAKE-ORIGINAL-BYTES" * 10)
    d = _driver(fake_photos)
    incoming = tmp_path / "restore" / ".mv_incoming"
    d.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(incoming)})
    link = gprpc.download_url(d, item.photo_id)
    assert isinstance(link, gprpc.DownloadUrl) and link.source == "VrseUb"
    assert fake_photos.rpc_ids_sent() == ["VrseUb"]
    d.get(link.reveal())
    assert (incoming / item.filename).read_bytes() == item.data  # the original bytes, no search at all


def test_download_url_falls_back_to_plftfd_with_exactly_one_key(fake_photos, tmp_path):
    item = fake_photos.add_item("movies", "Sample Movie [b2b2b2].chunk.002.mkv", data=b"FAKE-CHUNK-2" * 10)
    fake_photos.vrseub_original_url = False
    d = _driver(fake_photos, "movies")
    incoming = tmp_path / "incoming"
    d.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(incoming)})
    link = gprpc.download_url(d, item.photo_id)
    assert link.source == "pLFTfd" and fake_photos.rpc_ids_sent() == ["VrseUb", "pLFTfd"]
    assert fake_photos.rpc_log[-1]["request"] == [[item.photo_id], None, None]  # ONE key — never a zip (P14)
    d.get(link.reveal())
    assert (incoming / item.filename).read_bytes() == item.data
    assert [rec["filename"] for rec in fake_photos.downloads] == [item.filename]


def test_download_url_is_none_when_the_item_is_gone_or_no_link_is_minted(fake_photos):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    gone = fake_photos.add_item("tv", "Gone [c3c3c3].mkv", deleted=True)
    d = _driver(fake_photos)
    assert gprpc.download_url(d, gone.photo_id) is None and fake_photos.rpc_ids_sent() == ["VrseUb"]
    fake_photos.vrseub_original_url, fake_photos.mint_refused = False, True
    assert gprpc.download_url(d, item.photo_id) is None  # minting refused -> the caller uses Shift+D (P-20)
    assert fake_photos.rpc_ids_sent() == ["VrseUb", "VrseUb", "pLFTfd"]


def test_a_download_url_never_shows_through_print_repr_format_logging_json_or_a_traceback(fake_photos, capsys,
                                                                                            caplog):
    """PLAN Step 2b: the wrapper never reveals the link (disarm probe: a plain-string return fails here)."""
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    d = _driver(fake_photos)
    fake_photos.vrseub_original_url = False
    links = [gprpc.download_url(d, item.photo_id)]  # minted by pLFTfd …
    fake_photos.vrseub_original_url = True
    links.append(gprpc.download_url(d, item.photo_id))  # … and by the item-info answer
    log = logging.getLogger("test_gprpc.leak")
    with caplog.at_level(logging.DEBUG):
        for link in links:
            print(link, repr(link), f"{link} {link!r} {link!s}", "%s %r" % (link, link), "{} {!r}".format(link, link))
            print([link], {"link": link}, (link,))
            log.info("minted %s", link)
            log.info("minted %r", link)
            log.info(link)
            try:
                raise RuntimeError(f"could not fetch {link}")
            except RuntimeError:
                log.exception("fetch failed for %s", link)
    texts = [capsys.readouterr().out, caplog.text] + [_capture(_raise_with, link)[1][0] for link in links]
    urls = _minted_urls(fake_photos)
    assert len(urls) == 2
    for text in texts:
        assert "<redacted>" in text
        assert not any(url in text for url in urls) and gphotos_fake.DOWNLOAD_HOST not in text
    for link in links:
        with pytest.raises(TypeError):
            json.dumps({"link": link})


def test_the_download_url_wrapper_is_small_and_explicit():
    link = gprpc.DownloadUrl("https://video-downloads.fake.invalid/dl/FAKETOKEN0001", "pLFTfd")
    assert link.reveal() == "https://video-downloads.fake.invalid/dl/FAKETOKEN0001" and link.source == "pLFTfd"
    assert str(link) == repr(link) == format(link, "") == "DownloadUrl(<redacted>, source='pLFTfd')"
    with pytest.raises(TypeError):
        vars(link)  # __slots__: no __dict__ to dump
    with pytest.raises(AttributeError):
        link.url = "https://elsewhere.invalid/"  # and no stray attribute
    assert gprpc._wrap_link(None, "VrseUb") is None and gprpc._wrap_link("", "VrseUb") is None


# ================================================================================ signed_in_account, accounting

def test_signed_in_account_returns_the_email_for_the_terminal_and_sends_nothing(fake_photos):
    d = _driver(fake_photos, "movies")
    assert gprpc.signed_in_account(d) == fake_photos.accounts["movies"].email
    assert d.current_url == PHOTOS and fake_photos.rpc_log == []
    assert gprpc.stats(d) == {"calls": 0, "errors": 0, "breaker_open": False}
    fake_photos.accounts["movies"].wiz["oPEP7c"] = "not-an-email"
    assert gprpc.signed_in_account(d) is None


def test_the_end_of_run_line(fake_photos):
    webui_server = pytest.importorskip("webui.server")
    d = _driver(fake_photos)
    assert gprpc.summary_line(d) == "RPC calls: 0 (errors 0)"
    fake_photos.fail_next_rpc("http429")
    gprpc.call(d, "EzkLib", SEARCH)
    gprpc.item_info(d, UNKNOWN_ID)
    line = gprpc.summary_line(d)
    assert line == "RPC calls: 3 (errors 1)"
    assert not any(marker in line for marker in WEB_MARKERS)
    assert webui_server._parse_progress(line + "\n") == {"done": 0, "total": 0}  # invisible to the web progress


@pytest.mark.parametrize("account", ["movies", "tv", "anime", "others"])
def test_every_account_answers_in_its_own_session(fake_photos, account):
    """P-17: all four categories. Each account has its own page tokens; an item is found only in its own
    account — the wrong-account case the locator routes around (P-4)."""
    item = fake_photos.add_item(account, f"Sample [{account[:3]}123].mkv", data=f"FAKE-{account}".encode())
    elsewhere = next(a for a in ("movies", "tv", "anime", "others") if a != account)
    d = _driver(fake_photos, account)
    assert gprpc.item_info(d, item.photo_id)["filename"] == item.filename
    assert gprpc.hash_lookup(d, [item.sha1_hex])[item.sha1_hex][0]["photo_id"] == item.photo_id
    assert gprpc.signed_in_account(d) == fake_photos.accounts[account].email
    other = _driver(fake_photos, elsewhere)
    assert gprpc.item_info(other, item.photo_id) is None
    assert gprpc.hash_lookup(other, [item.sha1_hex]) == {item.sha1_hex: []}
    assert {e["account"] for e in fake_photos.rpc_log} == {account, elsewhere}
    assert all(e["at_ok"] for e in fake_photos.rpc_log)


# ================================================================================ nothing leaks, nothing is written

def test_no_token_email_or_url_in_any_output_message_or_traceback(fake_photos, capsys, caplog, monkeypatch):
    """Every helper and every error path, with the root logger at DEBUG: gprpc prints nothing, and no page
    token, account email or download URL appears in any output, exception message or locals-capturing
    traceback of an error it raises."""
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv", data=b"FAKE-LEAK-TEST")
    other = fake_photos.add_item("tv", "Other [o1o1o1].mkv")
    gone = fake_photos.add_item("tv", "Gone [c3c3c3].mkv", deleted=True)
    errors, texts = [], []

    def run(fn, *args, **kwargs):
        exc, formatted = _capture(fn, *args, **kwargs)
        if exc is not None:
            errors.append(type(exc).__name__)
        texts.extend(formatted)

    with caplog.at_level(logging.DEBUG):
        d = _driver(fake_photos)
        run(gprpc.signed_in_account, d)  # returns the email — the caller alone decides to show it
        run(gprpc.item_info, d, item.photo_id)
        run(gprpc.download_url, d, item.photo_id)
        fake_photos.vrseub_original_url = False
        run(gprpc.download_url, d, item.photo_id)
        fake_photos.vrseub_original_url = True
        run(gprpc.hash_lookup, d, [item.sha1_hex])
        run(gprpc.batch_info, d, [item.photo_id])
        run(gprpc.timeline_at, d, item.taken_ms)
        run(gprpc.search, d, "a1a1a1")
        run(gprpc.recent_uploads, d, [])
        run(gprpc.item_info, d, gone.photo_id)
        fake_photos.fail_next_rpc("http429")
        run(gprpc.call, d, "EzkLib", SEARCH)
        fake_photos.fail_next_rpc("changed_shape")
        run(gprpc.item_info, d, item.photo_id)  # RpcShapeError
        fake_photos.fail_next_rpc("empty_payload")
        run(gprpc.download_url, d, item.photo_id)  # RpcServerError
        run(gprpc.call, d, "XwAOJf", [["AF1QipFAKEanything00"], None])  # RpcRefused
        with monkeypatch.context() as m:  # a contradiction raised while a fresh link is in play
            m.setattr(fake_photos, "_h_VrseUb", lambda acc, req: (fake_photos._vrseub_payload(acc, other), None))
            run(gprpc.download_url, d, item.photo_id)
        with monkeypatch.context() as m:
            m.setattr(fake_photos, "_h_fDcn4b", lambda acc, req: ([gphotos_fake.ext_array(other)], None))
            run(gprpc.item_info, d, item.photo_id)
        fake_photos.fail_next_rpc("http500", count=4)
        run(gprpc.call, d, "EzkLib", SEARCH)  # retries exhausted — the 5th failure in a row opens the breaker
        run(gprpc.call, d, "EzkLib", SEARCH)  # RPC is off for this session
        assert gprpc.stats(d)["breaker_open"] is True
        fake_photos.wiz_token_missing["tv"] = True
        run(gprpc.call, _driver(fake_photos), "EzkLib", SEARCH)  # page tokens missing
        fake_photos.wiz_token_missing["tv"] = False
        fake_photos.session["tv"] = "expired"
        run(gprpc.call, _driver(fake_photos), "EzkLib", SEARCH)  # signed out
    assert {"RpcUnavailable", "RpcShapeError", "RpcServerError", "RpcRefused", "SessionExpiredError"} <= set(errors)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""  # gprpc prints nothing
    secrets = _minted_urls(fake_photos)
    assert len(secrets) >= 3
    for acc in fake_photos.accounts.values():
        secrets += [acc.wiz["SNlM0e"], acc.wiz["FdrFJe"], acc.email]
    secrets += [urllib.parse.quote(s, safe="") for s in secrets]  # … also as they travel in a request (P11)
    for text in texts + [caplog.text]:
        leaked = [s for s in secrets if s in text]
        assert not leaked, f"leaked {len(leaked)} secret(s)"
        assert gphotos_fake.DOWNLOAD_HOST not in text and gphotos_fake.THUMB_HOST not in text
    assert set(fake_photos.rpc_ids_sent()) <= READ_ONLY  # and only read-only ids ever reached Google Photos


def test_gprpc_never_writes_a_file(fake_photos, tmp_path, monkeypatch):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    writes = []
    real_open, real_os_open = builtins.open, os.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            writes.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
            writes.append(str(path))
        return real_os_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", guarded_open)
        m.setattr(io, "open", guarded_open)
        m.setattr(os, "open", guarded_os_open)
        d = _driver(fake_photos)
        gprpc.signed_in_account(d)
        gprpc.item_info(d, item.photo_id)
        gprpc.download_url(d, item.photo_id)
        gprpc.hash_lookup(d, [item.sha1_hex])
        gprpc.batch_info(d, [item.photo_id])
        gprpc.timeline_at(d, None)
        gprpc.search(d, "a1a1a1")
        gprpc.recent_uploads(d, [])
        fake_photos.fail_next_rpc("changed_shape")
        with pytest.raises(gprpc.RpcShapeError):
            gprpc.call(d, "EzkLib", SEARCH)
        gprpc.summary_line(d)
    assert writes == []
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
