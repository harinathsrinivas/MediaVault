import os
import json
import sys
import hashlib
import re
import secrets
import tempfile
import time
import random
import contextlib
import errno
import queue
import threading
import copy
import csv
import io
from datetime import datetime, timezone
from subprocess import SubprocessError

# [IMP-C25] The one project import: gpweb, the shared Google Photos parsers (names, dedupKeys,
# chunk names). gpweb imports no MediaVault module at import time (its dedup_key reaches
# gpcapture lazily), so this cannot create an import cycle.
import gpweb

# ==========================================
#         SHARED CONFIGURATION
# ==========================================
# Single source of truth for the library path/folder constants imported by
# both main.py and mainfetch.py. Importing only stdlib (plus the stdlib-only
# gpweb parser module) keeps this module free of any import-cycle risk (it
# must NEVER import main or mainfetch).
LIBRARY_MOVIES = r'C:\Media\library_movies.json'
LIBRARY_SERIES = r'C:\Media\library_series.json'
LIBRARY_ANIME = r'C:\Media\library_anime.json'
LIBRARY_OTHERS = r'C:\Media\library_others.json'

LOCAL_ROOT = r"C:\Media"  # Your PC Root
MKVMERGE_PATH = r"C:\Program Files\MKVToolNix\mkvmerge.exe"
MKVEXTRACT_PATH = r"C:\Program Files\MKVToolNix\mkvextract.exe"  # opaque-payload extraction (FLAC carry-out)

# Folder Naming Conventions
SPLIT_DIR_NAME = "_parts"  # Temp folder for chunks during push
CHECKSUM_DIR_NAME = "checksums"  # Permanent local folder for parity hashes
RESTORE_DIR_NAME = "restore"  # Folder where you dump downloaded files for restore
VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi', '.mov')

# Per-user state directory (locks, logs). Kept in the home dir so it survives
# regardless of the working directory and never collides with the media tree.
MV_STATE_DIR = os.path.join(os.path.expanduser("~"), ".mediavault")
MV_LOCK_DIR = os.path.join(MV_STATE_DIR, "locks")
MV_LOG_DIR = os.path.join(MV_STATE_DIR, "logs")
FETCH_SESSION_LOCK = os.path.join(MV_LOCK_DIR, "fetch_session.lock")
# [IMP-C25] Google Photos identity state: the learned sidecars + the failed list live in
# GPHOTOS_STATE_DIR; the inventory crawler's output (private item ids) in GP_INVENTORY_DIR
# (== tools/gp_inventory.py's default --out). Read both module-qualified at RUNTIME
# (mvcommon.GPHOTOS_STATE_DIR) — tests/conftest.py redirects them for every test.
GPHOTOS_STATE_DIR = os.path.join(MV_STATE_DIR, "gphotos")
GP_INVENTORY_DIR = r"D:\MediaVault_gp_inventory"


# ==========================================
#         RUNTIME CONFIG (mvconfig.json)
# ==========================================
# Optional, gitignored ``mvconfig.json`` at the repo root carries deploy-time
# settings the source tree must not hard-code (the web bind host/port, the TMDB
# API key). It is read ONCE and cached.
#
# Contract (see mvconfig.example.json):
#   {"web": {"host": "127.0.0.1", "port": 8765},
#    "tmdb": {"api_key": null}}
#
# NOTE (IMP-E15): web access tokens are NO LONGER a static config key. They are
# admin-minted, expiring, revocable tokens stored separately in ``mvtokens.json``
# (see the WEB ACCESS TOKENS section below). mvconfig.json no longer carries a
# ``web.token``.
#
# Resolution rules:
#   * An ABSENT file, or an absent key, falls back to the documented default
#     (host 127.0.0.1, port 8765) — today's behaviour, so local dev and the test
#     suite stay frictionless with no config file.
#   * A MALFORMED file (bad JSON / wrong top-level type) warns to STDERR and
#     falls back to defaults — it NEVER crashes the app.
#
# BINDING HAZARD: callers MUST use the getter functions below at RUNTIME. Do
# NOT bind these values into module-level constants at import time — a test (or
# a future caller) may monkeypatch a getter, and import-time binding would
# capture a stale value. The getters read the cached dict each call.
MVCONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mvconfig.json")

# Cache sentinel: None means "not yet loaded". After the first load this holds
# the parsed dict (or {} on absent/malformed) so the file is read at most once.
_CONFIG_CACHE = None

_WEB_DEFAULT_HOST = "127.0.0.1"
_WEB_DEFAULT_PORT = 8765


def _load_config():
    """Read and cache ``mvconfig.json`` once. Returns a dict (possibly empty).

    Absent file -> {} (defaults apply). Malformed JSON or a non-object top
    level -> warn to stderr + {} (never raises). The result is cached so the
    file is read at most once per process; clear ``_CONFIG_CACHE`` to force a
    reload (used by tests)."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    cache = {}
    if os.path.exists(MVCONFIG_PATH):
        try:
            with open(MVCONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cache = loaded
            else:
                print(
                    f"⚠️  mvconfig.json: expected a JSON object at the top level, "
                    f"got {type(loaded).__name__}; ignoring it (using defaults).",
                    file=sys.stderr,
                )
        except (json.JSONDecodeError, OSError) as e:
            print(
                f"⚠️  mvconfig.json is malformed and will be ignored "
                f"(using defaults). Error: {e}",
                file=sys.stderr,
            )
    _CONFIG_CACHE = cache
    return _CONFIG_CACHE


def _config_section(name):
    """Return the named top-level section as a dict, or {} if missing/not a dict.

    A section present but of the wrong type (e.g. ``"web": 5``) is tolerated as
    {} so a single bad section never poisons the others."""
    section = _load_config().get(name)
    return section if isinstance(section, dict) else {}


def web_host():
    """Configured web bind host, or the default 127.0.0.1.

    An empty/blank or non-string value falls back to the default so a bad entry
    can never produce an unusable bind address."""
    host = _config_section("web").get("host")
    if isinstance(host, str) and host.strip():
        return host
    return _WEB_DEFAULT_HOST


def web_port():
    """Configured web port, or the default 8765.

    Accepts an int or an all-digits string; anything else falls back to the
    default."""
    port = _config_section("web").get("port")
    if isinstance(port, bool):
        # bool is an int subclass — reject it explicitly so `true` isn't port 1.
        return _WEB_DEFAULT_PORT
    if isinstance(port, int):
        return port
    if isinstance(port, str) and port.strip().isdigit():
        return int(port)
    return _WEB_DEFAULT_PORT


def tmdb_api_key():
    """Configured TMDB API key, or "" when none is set.

    Returns "" (falsy) for absent / null / blank / non-string."""
    key = _config_section("tmdb").get("api_key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    return ""


def _config_string(section, key):
    """A trimmed string config value at section[key], or "" when absent/blank.

    Shared by the OMDb / RapidAPI key getters below — each is a runtime getter
    (never an import-time binding) so a test that monkeypatches it, or a config
    edit, is honoured on the next call. Returns "" (falsy) for absent / null /
    blank / non-string, matching tmdb_api_key()'s contract."""
    val = _config_section(section).get(key)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return ""


def omdb_api_key():
    """Configured OMDb API key (mvconfig.json ``omdb.api_key``), or "" when unset.

    OMDb (https://www.omdbapi.com) is the source of the cross-aggregator ratings
    (IMDb / Rotten Tomatoes / Metacritic) + Rated/Runtime/Awards/BoxOffice that the
    online-metadata refresh (IMP-E16) caches. Returns "" (falsy) for absent / null /
    blank / non-string, so a caller can `if not omdb_api_key(): bail`."""
    return _config_string("omdb", "api_key")


def rapidapi_imdb_key():
    """Configured RapidAPI IMDb key (mvconfig.json ``rapidapi.imdb_key``), or "".

    Reserved for a future RapidAPI-backed IMDb data source; a runtime getter today
    so it is available the moment a key is added to the config (no import-time
    binding). "" (falsy) when unset."""
    return _config_string("rapidapi", "imdb_key")


def rapidapi_rt_key():
    """Configured RapidAPI Rotten Tomatoes key (mvconfig.json ``rapidapi.rt_key``),
    or "".

    Reserved for a future RapidAPI-backed Rotten Tomatoes source; a runtime getter
    (no import-time binding). "" (falsy) when unset."""
    return _config_string("rapidapi", "rt_key")


def exa_api_key():
    """Configured EXA API key (mvconfig.json ``exa.api_key``), or "" when unset.

    EXA (https://exa.ai) is the web-search source for the trivia backfill (IMP-E16/A5):
    `fetch_trivia` queries EXA for a title's behind-the-scenes facts, GROQ distills
    them, and the result is cached in the gitignored mvextra.json. A runtime getter
    (no import-time binding) mirroring omdb_api_key()'s contract — "" (falsy) for
    absent / null / blank / non-string, so a caller can `if not exa_api_key(): bail`."""
    return _config_string("exa", "api_key")


def groq_api_key():
    """Configured GROQ API key (mvconfig.json ``groq.api_key``), or "" when unset.

    GROQ (https://groq.com) hosts the LLM that distills the EXA web snippets into the
    2-4 short, source-tagged trivia facts the trivia backfill (IMP-E16/A5) caches.
    A runtime getter (no import-time binding); "" (falsy) when unset."""
    return _config_string("groq", "api_key")


# ==========================================
#         WEB ACCESS TOKENS (mvtokens.json)
# ==========================================
# IMP-E15: web access is gated by ADMIN-MINTED, EXPIRING, REVOCABLE tokens — NOT
# a static config secret. The owner mints tokens from the genuine-local browser
# (the web Access panel) or the CLI (`python main.py token create`); each token
# can be shared with one device/person and individually revoked or left to
# expire. The store is a gitignored ``mvtokens.json`` at the repo root, SEPARATE
# from mvconfig.json.
#
# Store schema (atomic write: tempfile + os.replace):
#   {"tokens": [
#       {"id": "<8-hex>", "label": "<str>",
#        "hash": "<sha256 hex of the raw token>",
#        "created_at": "<iso8601 UTC>",
#        "expires_at": "<iso8601 UTC or null = never>"},
#       ...
#   ]}
#
# SECURITY: the RAW token is shown to the owner exactly ONCE (at mint time) and
# is NEVER persisted — only its sha256 is stored, so a leaked mvtokens.json does
# not reveal any usable token. Validation re-hashes the presented raw token and
# constant-time-irrelevant compares the hex digests (a sha256 of an attacker
# guess reveals nothing about timing of the real secret).
#
# BINDING HAZARD: callers MUST go through these functions at RUNTIME. The store
# path is the module-level ``MVTOKENS_PATH``; tests monkeypatch it to a temp file
# so a real mvtokens.json is never written. The store is read fresh on every call
# (it is tiny) — there is intentionally NO cache, so a mint/revoke is immediately
# visible to the next validate/list without a cache-clear dance.
MVTOKENS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mvtokens.json")


def _now_utc():
    """Current time as a timezone-aware UTC datetime (seam for monotonic tests)."""
    return datetime.now(timezone.utc)


def _parse_iso(ts):
    """Parse an iso8601 timestamp produced by ``.isoformat()`` back to an aware
    datetime, or None if it is null/blank/unparseable. A naive timestamp is
    coerced to UTC so comparisons never raise on tz-mismatch."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_tokens():
    """Read the token store and return its list of token records.

    An ABSENT file -> [] (no tokens minted yet -> the console is in
    unconfigured/local-only mode). A MALFORMED file (bad JSON / wrong shape)
    warns to stderr and is treated as [] so a corrupt store never crashes the
    app or silently locks the owner out (genuine-local admin still works with an
    empty token set). Always returns a list of dicts."""
    if not os.path.exists(MVTOKENS_PATH):
        return []
    try:
        with open(MVTOKENS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(
            f"⚠️  mvtokens.json is malformed and will be ignored "
            f"(no minted tokens recognized). Error: {e}",
            file=sys.stderr,
        )
        return []
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, list):
        return []
    return [t for t in tokens if isinstance(t, dict)]


def _save_tokens(tokens):
    """Atomically persist the token list to MVTOKENS_PATH (tempfile + os.replace).

    Mirrors save_library's durability idiom so a crash mid-write can never leave
    a half-written store. The parent dir is created if missing."""
    path = MVTOKENS_PATH
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tf:
            json.dump({"tokens": tokens}, tf, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _hash_token(raw):
    """sha256 hex digest of a raw token string (the only form ever stored)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mint_token(label, ttl_seconds):
    """Create a new access token and persist it.

    label: free-text human label (e.g. "iPhone", "Mum"). Coerced to str/stripped.
    ttl_seconds: lifetime in seconds, or None for a never-expiring token.

    Returns (id, raw_token, expires_at_iso_or_None). The RAW token is returned
    here and ONLY here — it is shown to the owner once and never stored (only its
    sha256 is). The id is an 8-hex handle for revoke/list.
    """
    raw = secrets.token_urlsafe(32)
    token_id = secrets.token_hex(4)  # 8 hex chars
    created = _now_utc()
    if ttl_seconds is None:
        expires_at = None
    else:
        # timedelta via fromtimestamp keeps it dependency-free; negative ttl
        # yields a past expiry (an already-expired token), which is a valid,
        # testable state.
        expires_at = datetime.fromtimestamp(
            created.timestamp() + ttl_seconds, tz=timezone.utc
        )
    record = {
        "id": token_id,
        "label": str(label or "").strip(),
        "hash": _hash_token(raw),
        "created_at": created.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
    }
    tokens = _load_tokens()
    tokens.append(record)
    _save_tokens(tokens)
    return token_id, raw, record["expires_at"]


def _is_expired(record, now=None):
    """True iff this record has an expires_at in the past. null/never -> False."""
    exp = _parse_iso(record.get("expires_at"))
    if exp is None:
        return False
    return exp <= (now or _now_utc())


def list_tokens():
    """Return a sanitized view of all stored tokens for display.

    Each item is {id, label, created_at, expires_at, expired} — the ``hash`` and
    the raw token are NEVER included (the raw is not even stored). ``expired`` is
    computed against the current time so a UI/CLI can flag stale tokens without
    re-deriving the rule."""
    now = _now_utc()
    out = []
    for t in _load_tokens():
        out.append({
            "id": t.get("id"),
            "label": t.get("label", ""),
            "created_at": t.get("created_at"),
            "expires_at": t.get("expires_at"),
            "expired": _is_expired(t, now),
        })
    return out


def revoke_token(token_id):
    """Remove the token with this id from the store. Idempotent.

    Returns True if a token was removed, False if no token had that id (so the
    caller can report "unknown id" while a DELETE endpoint can still treat the
    end state — token gone — as success)."""
    tokens = _load_tokens()
    kept = [t for t in tokens if t.get("id") != token_id]
    if len(kept) == len(tokens):
        return False
    _save_tokens(kept)
    return True


def validate_token(raw):
    """True iff ``raw`` matches a stored token that has not expired.

    sha256s the presented raw token and looks for a stored record with the same
    hash whose expires_at is null (never) or in the future. An expired match, an
    unknown token, or a blank/None input all return False."""
    if not raw or not isinstance(raw, str):
        return False
    presented = _hash_token(raw)
    now = _now_utc()
    for t in _load_tokens():
        if t.get("hash") == presented and not _is_expired(t, now):
            return True
    return False


# ==========================================
#               UTILITIES
# ==========================================
def retry(fn, attempts=3, backoff=(1, 4, 16), jitter=1.0,
          retry_on=(SubprocessError, TimeoutError), on_retry=None):
    """Call fn() with exponential-backoff retries on transient failures.

    Contract:
      - Returns fn()'s value on the first successful call.
      - On a retryable exception (type in `retry_on`), sleeps
        `backoff[i] + random.uniform(0, jitter)` before the next attempt; the
        backoff base is clamped to the last tuple entry when attempts exceed
        the tuple length. `jitter=0` yields the deterministic base.
      - After the final attempt the last `retry_on` exception is re-raised
        (the failure signal is unchanged for callers).
      - Exceptions NOT in `retry_on` propagate immediately, unchanged.
      - `attempts=1` means a single call with no retry.
      - `on_retry(attempt_number, exception)` runs before each sleep (the seam
        used to clean up / log). A callback failure never masks the retry.
    """
    for attempt in range(attempts):
        try:
            return fn()
        except retry_on as e:
            if attempt == attempts - 1:
                raise
            base = backoff[min(attempt, len(backoff) - 1)]
            delay = base + random.uniform(0, jitter)
            if on_retry is not None:
                try:
                    on_retry(attempt + 1, e)
                except Exception:
                    pass
            time.sleep(delay)


class LockHeldError(Exception):
    """Raised by fetch_session_lock(blocking=False) when the lock is already held."""
    pass


@contextlib.contextmanager
def fetch_session_lock(blocking=True, timeout=30, stale_after=3600):
    """Single-flight advisory lock for the interactive fetch session.

    A generic cross-platform OS-file lock built on the atomic
    ``os.O_CREAT | os.O_EXCL`` ("create only if absent") idiom — no fcntl/msvcrt
    platform branches needed for single-flight here. The lock file lives at
    ``FETCH_SESSION_LOCK`` and stores the holder's pid + acquisition timestamp.

    Behaviour:
      - Acquire succeeds by atomically creating the lock file.
      - If the lock already exists but its mtime is older than ``stale_after``
        seconds, it is treated as stale: removed and re-created (reclaim).
      - blocking=True (the interactive-fetch path): if held, poll up to
        ``timeout`` seconds for it to free; if it frees, acquire it; if the
        timeout elapses, RECLAIM it (remove + create) and proceed. A blocking
        acquire therefore NEVER hard-blocks the caller — an interactive fetch is
        never starved (proceeding on a stale/contended lock is the chosen
        behaviour).
      - blocking=False: if held (and not stale), raise LockHeldError.
      - On exit the lock file is always best-effort removed (a missing file
        never raises).
    """
    os.makedirs(MV_LOCK_DIR, exist_ok=True)

    def _write_lock(fd):
        try:
            os.write(fd, f"{os.getpid()} {time.time()}\n".encode())
        finally:
            os.close(fd)

    def _try_create():
        """Atomically create the lock file; return True on success, False on collision."""
        try:
            fd = os.open(FETCH_SESSION_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, OSError) as e:
            if isinstance(e, FileExistsError) or getattr(e, "errno", None) == errno.EEXIST:
                return False
            raise
        _write_lock(fd)
        return True

    def _is_stale():
        try:
            return (time.time() - os.path.getmtime(FETCH_SESSION_LOCK)) > stale_after
        except OSError:
            # File vanished between checks -> not stale; let the caller retry create.
            return False

    def _reclaim():
        """Remove an existing lock and recreate it; force the create to succeed."""
        try:
            os.remove(FETCH_SESSION_LOCK)
        except OSError:
            pass
        if not _try_create():
            # A racing creator slipped in; clear it and take the lock unconditionally.
            try:
                os.remove(FETCH_SESSION_LOCK)
            except OSError:
                pass
            fd = os.open(FETCH_SESSION_LOCK, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
            _write_lock(fd)

    if not _try_create():
        # Collision. A stale lock is reclaimable regardless of blocking mode.
        if _is_stale():
            _reclaim()
        elif blocking:
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(0.1)
                if _try_create():
                    break
                if _is_stale():
                    _reclaim()
                    break
            else:
                # Timed out and still held -> reclaim and proceed (never starve).
                _reclaim()
        else:
            raise LockHeldError(f"fetch session lock is held: {FETCH_SESSION_LOCK}")

    try:
        yield
    finally:
        try:
            os.remove(FETCH_SESSION_LOCK)
        except OSError:
            pass


def load_library():
    """Loads all three libraries and merges them into one dictionary."""
    data = {}

    for path in [LIBRARY_MOVIES, LIBRARY_SERIES, LIBRARY_ANIME, LIBRARY_OTHERS]:
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    data.update(json.load(f))
            except Exception as e:
                print(f"❌ CRITICAL: Library file is corrupt and cannot be loaded: {path}")
                print(f"   Error: {e}")
                print("   Refusing to continue to prevent data loss. Restore the file from backup.")
                sys.exit(1)

    return data


def save_library(data):
    """Splits the merged dictionary back into 4 files based on prefix."""
    mov_data = {}
    tv_data = {}
    ani_data = {}
    oth_data = {}

    for key, val in data.items():
        if key.startswith("mov"):
            mov_data[key] = val
        elif key.startswith("tv"):
            tv_data[key] = val
        elif key.startswith("ani"):
            ani_data[key] = val
        elif key.startswith("oth"):
            oth_data[key] = val
        else:
            # Fallback for legacy/unknown keys -> Movies (surfaced, never silent)
            print(f"⚠️  save_library: unrouted key '{key}' (no mov/tv/ani/oth prefix) → library_movies.json", file=sys.stderr)
            mov_data[key] = val

    # Atomic write: write to a temp file then os.replace() to prevent
    # partial-write corruption if the process is killed mid-save.
    for path, content in [(LIBRARY_MOVIES, mov_data), (LIBRARY_SERIES, tv_data), (LIBRARY_ANIME, ani_data), (LIBRARY_OTHERS, oth_data)]:
        dir_name = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as tf:
                json.dump(content, tf, indent=4)
            os.replace(tmp_path, path)
        except:
            os.unlink(tmp_path)
            raise


def generate_short_id(long_id):
    # Generates a stable 6-char hash for file naming
    hash_object = hashlib.md5(long_id.encode())
    return hash_object.hexdigest()[:6]


# [IMP-C25] SHA-1 side-channel. Google Photos identifies every uploaded item by its
# dedupKey = urlsafe-base64(SHA-1 of the exact bytes) (docs/feature-fetch-datetime/
# RESEARCH.md F21). calculate_file_hash computes that SHA-1 in the SAME read pass on a
# worker thread (hashlib releases the GIL, so the SHA-256 loop is not slowed) and caches
# it per (path, size, mtime); callers read it with cached_sha1(). The SHA-256 digest,
# the return value and the progress output are unchanged, and anything going wrong on
# the SHA-1 side only leaves the cache empty — it can never fail or alter a hash call.
_SHA1_CACHE = {}
_SHA1_CACHE_MAX = 512


def _sha1_cache_key(filepath):
    st = os.stat(filepath)
    return (os.path.normcase(os.path.abspath(filepath)), st.st_size, st.st_mtime_ns)


def cached_sha1(filepath):
    """SHA-1 hex computed alongside the last calculate_file_hash() of this exact file
    (same path, size and mtime) in this process, or None."""
    try:
        return _SHA1_CACHE.get(_sha1_cache_key(filepath))
    except OSError:
        return None


class _Sha1Sidecar:
    """Hashes the blocks it is fed with SHA-1 on a worker thread. Never raises."""

    def __init__(self):
        self._q = queue.Queue(maxsize=64)
        self._sha1 = hashlib.sha1()
        self._ok = True
        self._thread = threading.Thread(target=self._run, name="sha1-sidecar", daemon=True)
        self._thread.start()

    def _run(self):
        while True:  # always drain until the sentinel, so feed()/finish() can never block forever
            block = self._q.get()
            if block is None:
                return
            if self._ok:
                try:
                    self._sha1.update(block)
                except Exception:
                    self._ok = False

    def feed(self, block):
        if self._ok:
            try:
                self._q.put(block, timeout=60)
            except Exception:
                self._ok = False

    def finish(self):
        """Stop the worker; the hex digest, or None if anything went wrong."""
        try:
            self._q.put(None, timeout=60)
            self._thread.join(timeout=60)
        except Exception:
            return None
        return self._sha1.hexdigest() if self._ok and not self._thread.is_alive() else None


def _remember_sha1(filepath, sidecar):
    if sidecar is None:
        return
    try:
        sha1_hex = sidecar.finish()
        if sha1_hex:
            if len(_SHA1_CACHE) >= _SHA1_CACHE_MAX:
                _SHA1_CACHE.pop(next(iter(_SHA1_CACHE)))
            _SHA1_CACHE[_sha1_cache_key(filepath)] = sha1_hex
    except Exception:
        pass


def calculate_file_hash(filepath, block_size=65536):
    try:
        total = os.path.getsize(filepath)
    except OSError:
        print(f"  ❌ File not found: {os.path.basename(filepath)}")
        return None
    sha256 = hashlib.sha256()
    done = 0
    bar_width = 24
    fname = os.path.basename(filepath)
    try:
        sidecar = _Sha1Sidecar()  # [IMP-C25] same-pass SHA-1 (see above)
    except Exception:
        sidecar = None
    try:
        with open(filepath, 'rb') as f:
            for block in iter(lambda: f.read(block_size), b''):
                sha256.update(block)
                if sidecar is not None:
                    sidecar.feed(block)
                done += len(block)
                pct = done / total if total else 1.0
                filled = int(bar_width * pct)
                bar = '█' * filled + '░' * (bar_width - filled)
                size_str = f"{human_readable_size(done)} / {human_readable_size(total)}"
                print(f"\r  🔍 {fname}  [{bar}] {size_str} ", end='', flush=True)
        print()
        digest = sha256.hexdigest()
        _remember_sha1(filepath, sidecar)
        return digest
    except Exception as e:
        if sidecar is not None:
            sidecar.finish()
        print(f"\n  ❌ Error hashing {fname}: {e}")
        return None


def human_readable_size(size_bytes):
    if not size_bytes: return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def parse_size_str(size_str):
    """Parses '40gb', '500mb' into bytes."""
    size_str = size_str.lower().strip()
    match = re.match(r"(\d+(\.\d+)?)\s*([kmgt]?b)", size_str)
    if not match: return None
    val = float(match.group(1))
    unit = match.group(3)

    multipliers = {'b': 1, 'kb': 1024, 'mb': 1024 ** 2, 'gb': 1024 ** 3, 'tb': 1024 ** 4}
    return int(val * multipliers.get(unit, 1))


def episode_num_from_id(child_id, base_id):
    """Episode number from a library child ID, season-aware.

    Strips base_id as a prefix first (so glued anime ids like
    '…-s0202' with base '…-s02' leave '02' = episode 2, NOT 0202=202),
    then matches an optional e/E/x/X separator + number at end-of-string.
    Returns a float (handles half-eps like 16.5) or None if unparseable.
    """
    if base_id and child_id.startswith(base_id):
        ep_str = child_id[len(base_id):]
    else:
        ep_str = child_id            # base not a prefix -> parse the whole id
    m = re.search(r'^[eExX]?(\d+(?:\.\d+)?)$', ep_str)
    return float(m.group(1)) if m else None


# ==========================================
#         PROVIDER TOKENS (folder names)
# ==========================================
# A media folder can carry a PROVIDER TOKEN — `Dark (2017) [tmdbid-70523]` — that
# pins Plex/Emby/Jellyfin to an exact metadata match instead of a fuzzy title
# search. MediaVault both WRITES one (the enrich/rename stamp) and READS one (the
# "already stamped?" idempotency guard, and the artwork-inheritance walk that
# looks UP for the show folder).
#
# DETECTION is format-agnostic — all of these are real, and all must be found:
#   {tmdb-603692}    the CANONICAL form we emit; read by Plex, Emby AND Jellyfin
#   [tmdb-603692]    square, bare keyword — also read by all three
#   [tmdbid-603692]  Emby/Jellyfin square form; INVISIBLE TO PLEX (see below)
#   [tmdbid=603692]  Emby's `=` variant (square family only); invisible to Plex
# ...in any casing (`Run (2002) {TMDB-69590}` is a real library folder — IMP-C23).
#
# Which form each server actually reads was settled empirically on 2026-09-22, not
# from vendor docs (which disagree with each other and with community lore). A
# 20-folder matrix was scanned by real Plex, Emby and Jellyfin installs, using
# nonsense titles and deliberately wrong years so that ONLY a token could produce
# a correct match. Result: Plex rejects the `id` SUFFIX (`tmdbid`, `tvdbid`) and
# the `=` separator, and is indifferent to bracket style — it reads `{tmdb-…}`,
# `[tmdb-…]` and `(tmdb-…)` alike. The widely-repeated claim that Plex ignores
# square brackets is false. `{tmdb-<id>}` and `[tmdb-<id>]` both work on all
# three; `{tmdb-<id>}` was chosen (decision D11). Full matrix: DECISIONS.md D11.
#
# EMISSION is single-format: every emit site formats CANONICAL_TMDB_TOKEN_FMT /
# CANONICAL_TVDB_TOKEN_FMT, so changing what we WRITE is a one-line change here
# rather than a hunt through main.py.
#
# This lives in mvcommon (stdlib only; never imports main/mainfetch) because the
# detection regex had been hand-copied and then drifted between two call sites
# (IMP-C22, IMP-C23). There is now exactly ONE parser — find_provider_tokens —
# and every other token predicate is DERIVED from it, so the copies cannot come
# back. Callers must reach it module-qualified (`mvcommon.has_tmdb_token(...)`),
# per the binding-hazard note in the RUNTIME CONFIG section.

CANONICAL_TMDB_TOKEN_FMT = "{{tmdb-{id}}}"  # .format(id=…) -> "{tmdb-603692}"
# ^ the doubled braces are str.format() escaping, not a typo: "{{" emits one "{".
CANONICAL_TVDB_TOKEN_FMT = "{{tvdb-{id}}}"  # placeholder-only; never a real lookup

# STAGE 1 — bracket spans. Two deliberately dumb regexes with zero vocabulary
# knowledge: "an opening bracket, some bracket-free text, the MATCHING closing
# bracket". Excluding ALL FOUR bracket characters from the inner text is what
# keeps the two families from cross-matching: `{tmdb-123]` has no `}`, and a
# curly scan cannot reach across to borrow the `]` of a later square token
# (`{tmdb-123] [tmdbid-456}` matches nothing, in either family). That same
# exclusion proves the two families' spans can never overlap — which is why the
# combined result can simply be sorted back into left-to-right order.
_CURLY_SPAN_RE = re.compile(r"\{([^{}\[\]]*)\}")
_SQUARE_SPAN_RE = re.compile(r"\[([^{}\[\]]*)\]")

# STAGE 2 — the vocabulary. Both the bare tag (`tmdb`, TRaSH-Guides) and the
# `…id` tag (`tmdbid`, Emby/Jellyfin) are in the wild and mean the same thing.
# Adding a 4th provider (anidb, …) is ONE string in _PROVIDERS.
_PROVIDERS = ("tmdb", "tvdb", "imdb")
_PROVIDER_BY_TAG = {tag: provider for provider in _PROVIDERS
                    for tag in (provider, provider + "id")}

# `<tag><separator><id>`, anchored (fullmatch) and nothing else — so a bare
# release-group tag (`[rartv]`, `[FraMeSToR]`) or a chunk short_id (`[a1b2c3]`)
# has no separator and is rejected before the vocabulary is even consulted.
_TOKEN_CONTENT_RE = re.compile(r"([A-Za-z]+)([-=])(.+)")


def _parse_token_content(content):
    """Stage 2: decide whether `content` — the text INSIDE one bracket pair — is
    a provider token, and take it apart.

    Returns (provider, tag, separator, id), or None when it is not a token.
    `provider` is the canonical name ('tmdb') and `tag` the spelling actually
    used ('tmdbid'), both lower-cased; `separator` and `id` are verbatim, so
    tag+separator+id reconstructs `content` apart from its casing.

    Deliberately bracket-AGNOSTIC: it never learns which family produced
    `content`, so `{…}` and `[…]` cannot grow different vocabularies. The one
    rule that IS family-specific (`=` is square-only) is applied by the caller.
    """
    m = _TOKEN_CONTENT_RE.fullmatch(content)
    if not m:
        return None
    tag = m.group(1).lower()
    provider = _PROVIDER_BY_TAG.get(tag)
    if provider is None:
        return None
    return provider, tag, m.group(2), m.group(3)


def find_provider_tokens(name):
    """Every recognized provider token in `name`, in left-to-right order.

    There can be more than one — a release folder may carry a source-provided
    `[tvdbid-…]` next to MediaVault's own `[tmdbid-…]`. Each item is:

        {"provider": "tmdb"|"tvdb"|"imdb",      # canonical name, lower-case
         "id":       "<the id substring>",      # verbatim: "603692", "tt0111161"
         "bracket":  "curly"|"square",          # which family it was written in
         "match":    "<the matched substring>", # brackets included
         "span":     (start, end)}              # name[start:end] == match

    Spans never overlap, so a caller can rewrite ONE token by its span and leave
    every other bracketed chunk (`[tvdbid-…]`, `[rartv]`, a chunk short_id)
    byte-identical — which is what the token migration does.
    """
    name = name or ""
    found = []
    for bracket, span_re in (("curly", _CURLY_SPAN_RE), ("square", _SQUARE_SPAN_RE)):
        for m in span_re.finditer(name):
            parsed = _parse_token_content(m.group(1))
            if parsed is None:
                continue
            provider, _tag, separator, token_id = parsed
            # `=` is Emby's square-bracket variant. `{tmdb=…}` is not a
            # convention anywhere, so it stays a non-token.
            if separator == "=" and bracket != "square":
                continue
            found.append({"provider": provider, "id": token_id, "bracket": bracket,
                          "match": m.group(0), "span": m.span()})
    found.sort(key=lambda token: token["span"])
    return found


def has_tmdb_token(name):
    """True if `name` already carries a TMDB provider token in ANY recognized
    format — `{tmdb-…}`, `[tmdb-…]`, `[tmdbid-…]`, `[tmdbid=…]`, any casing.

    The idempotency guard behind every stamp site: a folder that already has a
    token must never be given a second one. TMDB-ONLY on purpose — a
    source-provided `[tvdbid-…]` is somebody else's tag and must not block
    MediaVault's own tmdb stamp. Derived from find_provider_tokens rather than
    re-matching, so this predicate and the parser cannot drift apart (which is
    exactly what happened to the two hand-written copies in IMP-C22/C23).
    """
    return any(token["provider"] == "tmdb" for token in find_provider_tokens(name))


# ==========================================
#   GOOGLE PHOTOS IDENTITY (IMP-C25)
# ==========================================
# The primitives every later IMP-C25 step builds on (docs/feature-fetch-datetime/PLAN.md Step 3; the
# P-1…P-20 decisions in DECISIONS.md):
#   * the versioned `gphotos` identity block (P-1), its pin rule (P-2) and the learned account (P-4);
#   * the uploaded-object inventory — every object MediaVault uploads to Google Photos: an unsplit leaf,
#     each split chunk, each FLAC holder, an extras item, each chunk of a split extras item (P-3: a split
#     entry uploads its chunks + holders, never the whole file) — with its exact uploaded name, owner
#     slot, trusted block and cloud state;
#   * the reader of gpcapture's `<folder>/<short_id>.gpcapture.json` (capture v1 + v2);
#   * the learned sidecar `learned_<account>.jsonl` (the P-10 learned-record line — every producer writes
#     through append_learned), its reducer and the PURE apply step the apply engine (PLAN Step 6) uses;
#   * the failed list `failed.jsonl` with its regenerated Excel view `failed_objects.csv` (P-18), and the
#     offline pending list (P-11).
# Rules: stdlib + gpweb only (never main / mainfetch); library-READ-only — the apply engine is the only
# writer of `gphotos` blocks; every state path is derived from GPHOTOS_STATE_DIR at call time.

GPHOTOS_BLOCK_VERSION = 1

# Account keys (== mainfetch.CHROME_PROFILES keys) and the id-prefix -> category map (== mainfetch.
# ID_PREFIX_PROFILE; a category is named after its default account). Duplicated because this module must
# never import mainfetch — tests/test_gphotos_primitives.py pins both against mainfetch.
GP_ACCOUNTS = ("movies", "tv", "anime", "others")
GP_CATEGORY_PREFIXES = (("mov", "movies"), ("tv", "tv"), ("ani", "anime"), ("oth", "others"))
GP_PROOF_LADDER = ("dedupkey", "sha256", "filename", "manual")  # P-5, strongest first
GP_SOURCES = ("map", "check", "fetch", "confirm", "manual")      # the step that produced a learned record
# The P-1 approach vocabulary in waterfall order (P-8): the order of `approach.ok` / `approach.failed`
# and the order `approach.best` is picked in.
GP_APPROACHES = ("photo_id", "hash", "taken_second", "exact_name", "title_sid", "user_query", "legacy")
# Learned-record `approach.tried` (a waterfall stage) -> its P-1 approach: P-10's five stages plus the two
# P-8 added with the OD-9 / OD-5 rulings — S0b content-hash lookup and S3u the user's search override.
GP_STAGES = {"s0_photo_id": "photo_id", "s0_hash": "hash", "s1_taken_second": "taken_second",
             "s2_exact_name": "exact_name", "s3_title_sid": "title_sid", "s3u_user_query": "user_query",
             "s4_legacy": "legacy"}
GP_OUTCOMES = ("exactly-one", "ambiguous", "not-found", "wrong-item", "error")
GP_FAILED_REASONS = ("not-found", "ambiguous", "wrong-item", "s4-skipped", "probable", "missing-after-backup",
                     "missing-everywhere", "partial-set", "inconsistent")
GP_DECISIONS = ("url", "datetime", "search", "not_uploaded", "skip")
# failed_objects.csv (P-18) == map_photos' resolutions_todo.csv (PLAN Step 9): the plan's 17 columns plus
# `sha256` last — the exact object key, so an edited row maps back to its object even if labels shift.
GP_FAILED_CSV_COLUMNS = ("account", "category", "manual_id", "object", "uploaded_name", "status", "candidates",
                         "suggested_photo_url", "expected_taken_local", "decision", "photo_url", "date", "time",
                         "gmt", "new_search_term", "set_search_cmd", "note", "sha256")

_LEARNED_VERSION = 1
_CAPTURE_SUFFIX = ".gpcapture.json"               # == gpcapture.CAPTURE_SUFFIX (pinned by a test)
_PHOTO_URL = "https://photos.google.com/photo/"   # an item's ADDRESS (never a download URL — P-20)
_CLOUD_STATUSES = ("onboarded", "archived", "restored_local")  # cmd_prep's cloud-bearing statuses
_VERIFIED_PROOFS = ("dedupkey", "sha256", "filename")          # a verified copy (P-4); `manual` needs a check
_CONFIRMING_SOURCES = ("map", "check", "fetch", "confirm")     # `confirmed` only from these (PLAN Step 6)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SHA1_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_DATE_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HOLDER_NAME_RE = re.compile(r"\.holder\.mkv$", re.IGNORECASE)
_WIDTH_HEIGHT_RE = re.compile(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$")
_BLOCK_KEYS = ("v", "sha256", "sha1", "dedup_key", "size_bytes", "width", "height", "duration_s", "date_utc",
               "uploaded_name", "pushed", "accounts", "home_account", "search_override")
_PUSHED_KEYS = ("at", "device_id", "device_tz", "device_tz_offset_min")
_ACCOUNT_KEYS = ("photo_id", "taken_ms", "tz_offset_ms", "upload_ms", "dedup_key", "gp_filename", "width",
                 "height", "verified_at", "verified_by", "source", "approach", "alternates", "original_quality",
                 "partial_upload", "confirmed")
# Facts about the Google Photos ITEM a photo_id names — they travel with that photo_id.
_ITEM_KEYS = ("taken_ms", "tz_offset_ms", "upload_ms", "dedup_key", "gp_filename", "width", "height",
              "original_quality", "partial_upload")
_APPROACH_KEYS = ("best", "ok", "failed", "at", "via")
_LEARNED_KEYS = ("v", "at", "account", "manual_id", "label", "sha256", "photo_id", "taken_ms", "tz_offset_ms",
                 "upload_ms", "dedup_key", "gp_filename", "width", "height", "proof", "source", "approach", "stale",
                 "alternates", "original_quality", "partial_upload", "search_override")
_TRY_KEYS = ("tried", "outcome", "n", "via")
_REDUCED_KEYS = (("account", "sha256", "manual_id", "label", "photo_id") + _ITEM_KEYS
                 + ("proof", "source", "at", "content_key", "approach", "alternates", "dead", "search_override"))
_FAILED_KEYS = ("at", "run", "account", "category", "manual_id", "label", "sha256", "uploaded_name",
                "expected_taken_local", "tried", "candidates", "reason")
_TRIED_KEYS = ("approach", "outcome", "n")
_OBJECT_KINDS_WHOLE = ("whole", "extra_whole")
_OBJECT_KINDS_PART = ("chunk", "holder", "extra_chunk")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_sha256(v):
    return isinstance(v, str) and _SHA256_RE.match(v) is not None


def _text(v):
    """True for a string with visible content."""
    return isinstance(v, str) and v.strip() != ""


def _iso_now():
    """Now as the '…Z' timestamp every IMP-C25 record carries (seconds; via the _now_utc test seam)."""
    return _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_at(at):
    """'YYYY-MM-DDTHH:MM:SS[.ffffff]Z' -> aware UTC datetime, else None."""
    if not isinstance(at, str) or not _AT_RE.match(at):
        return None
    try:
        return datetime.fromisoformat(at[:-1] + "+00:00")
    except ValueError:
        return None


def _at_ts(at):
    """Sort key of a '…Z' timestamp: epoch seconds; -inf (the oldest) when absent or invalid."""
    dt = _parse_at(at)
    return dt.timestamp() if dt is not None else float("-inf")


def _clean(obj):
    """Drop None values at any depth, and the dicts / lists that leaves empty (P-1: only known facts)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            v = _clean(v)
            if v is None or (isinstance(v, (dict, list)) and not v):
                continue
            out[k] = v
        return out
    if isinstance(obj, list):
        return [v for v in (_clean(x) for x in obj) if v is not None]
    return obj


def _ordered(d, keys):
    """`d` with `keys` first, in that order, then its other keys in their original order."""
    out = {k: d[k] for k in keys if k in d}
    out.update((k, v) for k, v in d.items() if k not in out)
    return out


def _strength(proof):
    """P-5 rank of a proof: 4 dedupkey, 3 sha256, 2 filename, 1 manual, 0 none."""
    return len(GP_PROOF_LADDER) - GP_PROOF_LADDER.index(proof) if proof in GP_PROOF_LADDER else 0


# ------------------------------------------------------------- the `gphotos` block (P-1, P-2, P-4)

def new_gphotos_block(sha256, **facts):
    """A fresh v1 `gphotos` block (PLAN P-1) pinned to `sha256` — the owning object's CURRENT SHA-256 (P-2).

    `facts` are the other P-1 top-level keys: sha1, dedup_key, size_bytes, width, height, duration_s,
    date_utc, uploaded_name, pushed, accounts, home_account, search_override. None values — at any depth —
    and the containers they leave empty are dropped (only known facts are written); given one of sha1 /
    dedup_key the other is derived (they encode the same 20 bytes). Keys come out in the P-1 order, so equal
    facts always serialize to identical JSON.

    Raises ValueError for a sha256 that is not 64 hex characters (a block without a valid pin could never be
    trusted), an unknown key, a malformed value, a sha1 and dedup_key that disagree, or an account outside
    GP_ACCOUNTS. The apply engine (PLAN Step 6) is the only code that stores the result in the library."""
    if not _is_sha256(sha256):
        raise ValueError("new_gphotos_block: sha256 must be the owning object's 64-hex SHA-256 (the P-2 pin)")
    unknown = sorted(str(k) for k in facts if k not in _BLOCK_KEYS[2:])
    if unknown:
        raise ValueError(f"new_gphotos_block: unknown key(s) {unknown}")
    facts = _clean(facts)
    sha1 = facts.get("sha1")
    if sha1 is not None:
        if not (isinstance(sha1, str) and _SHA1_RE.match(sha1)):
            raise ValueError("new_gphotos_block: sha1 must be 40 hex characters")
        sha1 = sha1.lower()
    key = facts.get("dedup_key")
    if key is not None:
        key = gpweb.normalize_dedup_key(key)
        if key is None:
            raise ValueError("new_gphotos_block: dedup_key is not a valid dedupKey")
    if sha1 and key and gpweb.dedup_key(sha1) != key:
        raise ValueError("new_gphotos_block: sha1 and dedup_key describe different bytes")
    if sha1 or key:
        facts["sha1"] = sha1 or gpweb.dedup_key_to_sha1_hex(key)
        facts["dedup_key"] = key or gpweb.dedup_key(sha1)
    for k in ("size_bytes", "width", "height"):
        if k in facts and not (_is_int(facts[k]) and facts[k] >= 0):
            raise ValueError(f"new_gphotos_block: {k} must be a non-negative integer")
    duration = facts.get("duration_s")
    if duration is not None and not (isinstance(duration, (int, float)) and not isinstance(duration, bool)
                                     and duration >= 0):
        raise ValueError("new_gphotos_block: duration_s must be a non-negative number")
    if "date_utc" in facts and not (isinstance(facts["date_utc"], str) and _DATE_UTC_RE.match(facts["date_utc"])):
        raise ValueError("new_gphotos_block: date_utc must look like YYYY-MM-DDTHH:MM:SSZ")
    if "uploaded_name" in facts and not _text(facts["uploaded_name"]):
        raise ValueError("new_gphotos_block: uploaded_name must be a non-empty string")
    for k in ("pushed", "search_override"):
        if k in facts and not isinstance(facts[k], dict):
            raise ValueError(f"new_gphotos_block: {k} must be a dict")
    if "pushed" in facts:
        facts["pushed"] = _ordered(facts["pushed"], _PUSHED_KEYS)
    if "accounts" in facts:
        accounts = facts["accounts"]
        if not (isinstance(accounts, dict)
                and all(a in GP_ACCOUNTS and isinstance(r, dict) for a, r in accounts.items())):
            raise ValueError(f"new_gphotos_block: accounts must map account keys {GP_ACCOUNTS} to records")
        facts["accounts"] = {a: _ordered(accounts[a], _ACCOUNT_KEYS) for a in GP_ACCOUNTS if a in accounts}
    if "home_account" in facts and facts["home_account"] not in GP_ACCOUNTS:
        raise ValueError(f"new_gphotos_block: home_account must be one of {GP_ACCOUNTS}")
    return _ordered({"v": GPHOTOS_BLOCK_VERSION, "sha256": sha256, **facts}, _BLOCK_KEYS)


def block_trusted(block, sha256):
    """P-2 pin rule: a `gphotos` block is TRUSTED only when it is a v1 block whose pin (`sha256`) equals the
    owning object's CURRENT SHA-256 — the leaf's `hash`, a chunk's `hash`, a holder's `holder_hash` or an
    extras item's `hash`. Readers ignore an untrusted block (it describes other bytes, e.g. from before a
    re-split or a re-master); the apply engine replaces it."""
    if not (isinstance(block, dict) and _is_sha256(sha256)):
        return False
    version = block.get("v")
    return _is_int(version) and version == GPHOTOS_BLOCK_VERSION and block.get("sha256") == sha256


def account_record(block, account):
    """A copy of the block's record for `account` (P-1 `accounts[account]`), or None. Pass a TRUSTED block."""
    accounts = block.get("accounts") if isinstance(block, dict) else None
    record = accounts.get(account) if isinstance(accounts, dict) else None
    return copy.deepcopy(record) if isinstance(record, dict) else None


def _prefix_account(manual_id, prefix_map, default):
    """mainfetch.profile_for_id's rule over a caller-supplied ordered [(id prefix, account)] map."""
    if isinstance(manual_id, str):
        for prefix, key in prefix_map:
            if manual_id.startswith(prefix):
                return key
    return default


def category_for_id(manual_id):
    """The content category of a library id from its prefix — movies | tv | anime | others (named after the
    default account, mainfetch.ID_PREFIX_PROFILE) — or None for an unknown prefix."""
    return _prefix_account(manual_id, GP_CATEGORY_PREFIXES, None)


def home_account(block, manual_id, prefix_map, default):
    """The Google account an object lives in — P-4's learned layer. Pass a TRUSTED block (the `block` of
    iter_entry_objects / iter_uploaded_objects) or None, and the routing constants: prefix_map =
    mainfetch.ID_PREFIX_PROFILE, default = mainfetch.DEFAULT_PROFILE.

      1. an account holding a VERIFIED copy (verified_by dedupkey / sha256 / filename): the id's prefix-default
         account when it holds one, else the one with the strongest proof (then the newest verification, then
         the name) — P-4's rule for items that exist in several accounts;
      2. likewise for a copy only the user vouched for (verified_by manual: still verified before any download,
         so a better guess than the prefix);
      3. the block's stored `home_account` (the apply engine stores this function's result, computed with the
         stored field removed);
      4. the prefix default — exactly mainfetch.profile_for_id's answer (drift-pinned by a test).

    Accounts outside the map are ignored. The top routing layer — mvconfig.json `fetch_account_overrides`
    (Step H) — belongs to the caller (PLAN Step 10), which consults it before this function."""
    known = {key for _prefix, key in prefix_map}
    known.add(default)
    prefix_home = _prefix_account(manual_id, prefix_map, default)
    accounts = block.get("accounts") if isinstance(block, dict) else None
    records = ({a: r for a, r in accounts.items() if a in known and isinstance(r, dict)}
               if isinstance(accounts, dict) else {})
    for proofs in (_VERIFIED_PROOFS, ("manual",)):
        holders = {a: r for a, r in records.items() if r.get("verified_by") in proofs}
        if prefix_home in holders:
            return prefix_home
        if holders:
            return min(holders, key=lambda a: (-_strength(holders[a]["verified_by"]),
                                               -_at_ts(holders[a].get("verified_at")), a))
    stored = block.get("home_account") if isinstance(block, dict) else None
    return stored if isinstance(stored, str) and stored in known else prefix_home


# ------------------------------------------------------------- uploaded objects (P-3, P-6)

def remote_upload_name(filename, short_id, kind=None):
    """The exact name an object is pushed to the phone under — hence its Google Photos file name (P-6).

    A WHOLE file (an unsplit leaf, a whole extras item) is renamed to '<stem> [<short_id>]<ext>' (cmd_push,
    push_one_extra; == cmd_prep's default search_term); a split chunk and a FLAC holder keep their own names,
    which already carry the tag (split_video_file, _carry_out_flac_track). An object `kind` decides when
    given; without it the name does (`.chunk.NNN.mkv` / `.holder.mkv` are kept). None without a name, or for
    a whole file without a short_id. Drift-pinned against cmd_push (the device file name), cmd_prep's
    search_term, push_one_extra and gpcapture.snapshot_push_objects.

    >>> remote_upload_name("Example.Show.S01E02.1080p.mkv", "a1b2c3")
    'Example.Show.S01E02.1080p [a1b2c3].mkv'
    >>> remote_upload_name("Example.Movie.2019 [d4e5f6].chunk.002.mkv", "d4e5f6")
    'Example.Movie.2019 [d4e5f6].chunk.002.mkv'
    """
    if not _text(filename):
        return None
    if kind is None:
        keep = gpweb.chunk_index(filename) is not None or _HOLDER_NAME_RE.search(filename) is not None
    elif kind in _OBJECT_KINDS_PART:
        keep = True
    elif kind in _OBJECT_KINDS_WHOLE:
        keep = False
    else:
        raise ValueError(f"remote_upload_name: unknown object kind {kind!r}")
    if keep:
        return filename
    if not _text(short_id):
        return None
    stem, ext = os.path.splitext(filename)
    return f"{stem} [{short_id}]{ext}"


def _owner_uploaded(owner):
    """The library's own claim that an owner (a leaf or an extras item) reached the cloud — cmd_prep's
    cloud-bearing test: `uploaded` truthy or status onboarded / archived / restored_local."""
    return bool(owner.get("uploaded")) or owner.get("status") in _CLOUD_STATUSES


def _numbered(items, keep=None):
    """[(list position, dict)] for the dict members of `items` (a list) that `keep` accepts."""
    return [(pos, d) for pos, d in enumerate(items if isinstance(items, list) else [])
            if isinstance(d, dict) and (keep is None or keep(d))]


def _make_object(manual_id, kind, index, label, node, hash_key, short_id, slot, captures, uploaded,
                 push_name=None, group_rel=None, sub_rel=None):
    """One uploaded-object dict (shape: iter_entry_objects) for the library dict `node` that owns it."""
    sha256 = node.get(hash_key)
    sha256 = sha256 if isinstance(sha256, str) and sha256 else None
    filename = node.get("holder_filename" if kind == "holder" else "filename")
    filename = filename if _text(filename) else None
    raw = node.get("gphotos")
    block = copy.deepcopy(raw) if block_trusted(raw, sha256) else None
    capture = captures.get(sha256) if sha256 else None
    if block is not None and _text(block.get("uploaded_name")):
        uploaded_name = block["uploaded_name"]
    elif capture is not None and _text(capture.get("uploaded_name")):
        uploaded_name = capture["uploaded_name"]
    else:
        uploaded_name = remote_upload_name(push_name or filename, short_id, kind)
    accounts = block.get("accounts") if block is not None else None
    if isinstance(accounts, dict) and any(isinstance(r, dict) and r.get("confirmed") is True
                                          for r in accounts.values()):
        cloud_state = "confirmed"
    elif capture is not None and "pushed" in capture:
        cloud_state = "unverified"
    else:
        cloud_state = "uploaded" if uploaded else "local"
    return {"manual_id": manual_id, "category": category_for_id(manual_id), "kind": kind, "index": index,
            "label": label, "sha256": sha256, "uploaded_name": uploaded_name, "slot": slot, "block": block,
            "cloud_state": cloud_state, "capture": capture, "short_id": short_id if _text(short_id) else None,
            "filename": filename, "group_rel": group_rel, "sub_rel": sub_rel}


def _leaf_objects(manual_id, entry):
    """A leaf's own objects: the whole file, or — split (P-3) — its chunks then its FLAC holders."""
    short_id = entry.get("short_id")
    captures = read_capture(entry.get("folder_path"), short_id)
    uploaded = _owner_uploaded(entry)
    split = entry.get("split_info")
    if not (isinstance(split, dict) and split.get("is_split")):
        yield _make_object(manual_id, "whole", None, "whole", entry, "hash", short_id, (manual_id,),
                           captures, uploaded)
        return
    chunks = _numbered(split.get("chunks"))
    for n, (pos, chunk) in enumerate(chunks, 1):
        yield _make_object(manual_id, "chunk", n, f"chunk {n}/{len(chunks)}", chunk, "hash", short_id,
                           (manual_id, "split_info", "chunks", pos), captures, uploaded)
    holders = _numbered(split.get("carried_out_tracks"), lambda t: _text(t.get("holder_filename")))
    for n, (pos, track) in enumerate(holders, 1):
        label = "holder" if len(holders) == 1 else f"holder {n}/{len(holders)}"
        yield _make_object(manual_id, "holder", n, label, track, "holder_hash", short_id,
                           (manual_id, "split_info", "carried_out_tracks", pos), captures, uploaded)


def _extras_objects(title_id, entry):
    """A title's extras objects (IMP-D19): each whole item, or each chunk of a split item; groups sorted
    (push_title_extras' order), items in library order. An item's capture lives beside its file."""
    extras = entry.get("extras")
    groups = extras.get("groups") if isinstance(extras, dict) else None
    if not isinstance(groups, dict):
        return
    title_folder = entry.get("folder_path")
    for group_rel in sorted(g for g in groups if isinstance(g, str)):
        group = groups[group_rel]
        for pos, item in _numbered(group.get("items") if isinstance(group, dict) else None):
            sub_rel = item.get("sub_rel") or item.get("filename") or ""
            sub_rel = sub_rel if isinstance(sub_rel, str) else ""
            short_id = item.get("short_id")
            captures = {}
            if _text(title_folder) and sub_rel:
                item_path = os.path.join(title_folder, group_rel.replace("/", os.sep), sub_rel.replace("/", os.sep))
                captures = read_capture(os.path.dirname(item_path), short_id)
            uploaded = _owner_uploaded(item)
            label = f"extra {group_rel}/{sub_rel}"
            slot = (title_id, "extras", "groups", group_rel, "items", pos)
            split = item.get("split_info")
            if isinstance(split, dict) and split.get("is_split"):
                chunks = _numbered(split.get("chunks"))
                for n, (cpos, chunk) in enumerate(chunks, 1):
                    yield _make_object(title_id, "extra_chunk", n, f"{label} chunk {n}/{len(chunks)}", chunk,
                                       "hash", short_id, slot + ("split_info", "chunks", cpos), captures,
                                       uploaded, group_rel=group_rel, sub_rel=sub_rel)
            else:
                # push_one_extra uploads basename(<title>/<group_rel>/<sub_rel>): sub_rel's last part
                push_name = os.path.basename(sub_rel.replace("/", os.sep)) or None
                yield _make_object(title_id, "extra_whole", None, label, item, "hash", short_id, slot,
                                   captures, uploaded, push_name=push_name, group_rel=group_rel,
                                   sub_rel=sub_rel)


def iter_entry_objects(manual_id, entry):
    """Every object MediaVault uploads for ONE library entry, in a fixed order, as dicts:

      manual_id      the owning library id (the TITLE id for extras)
      category       movies | tv | anime | others from the id prefix (None when unknown)
      kind           whole | chunk | holder | extra_whole | extra_chunk
      index          1-based position among the entry's chunks / holders / an extra's chunks (None: whole)
      label          'whole' · 'chunk 2/3' · 'holder' · 'extra Specials/BTS.mkv' · 'extra …/x.mkv chunk 1/2'
      sha256         the object's CURRENT hash (leaf hash · chunk hash · holder_hash · item hash) = the P-2 pin
      uploaded_name  the exact name it was uploaded under: trusted block > capture record > remote_upload_name
      slot           path from the library root to the dict that holds (or will hold) its `gphotos` block —
                     slot_owner() resolves it; JSON-friendly (dict keys and list positions)
      block          a copy of its TRUSTED `gphotos` block, or None (absent, or untrusted — P-2: ignored)
      cloud_state    confirmed   a trusted block holds a confirmed account record
                     unverified  a capture records its push; not confirmed yet (P-11: `pending`)
                     uploaded    the library says it was pushed; no capture (a legacy object — the mapper's)
                     local       not uploaded
      capture        its read_capture() facts (matched by sha256), or None
      short_id, filename, group_rel, sub_rel   the name parts (group_rel / sub_rel for extras only)

    P-3: a split leaf yields its chunks and FLAC holders, never the whole file. Alias-safe: a multi_ep_alias
    yields nothing (its primary is iterated in its own right); a season_map yields only its extras. Any other
    entry is a leaf (the whole-library iterators' convention in main.py). Library-read-only; it reads the
    capture files beside the objects (read_capture never raises)."""
    if not isinstance(entry, dict):
        return
    entry_type = entry.get("type")
    if entry_type == "multi_ep_alias":
        return
    if entry_type != "season_map":
        yield from _leaf_objects(manual_id, entry)
    yield from _extras_objects(manual_id, entry)


def iter_uploaded_objects(library, ids=None):
    """Every object believed to be in — or on its way to — Google Photos (cloud_state != 'local'), each once:
    for the whole library (ids None: every id, sorted) or for `ids` (a list, or one id, in that order).

    Alias-safe: a whole-library pass skips multi_ep_alias entries (their primaries come on their own); an
    explicitly requested alias resolves one hop to its primary (main._resolve_alias), a dangling one is
    skipped. A season_map contributes its extras only — its episodes are ids of their own (expand a season
    before calling to get them). Unknown ids are skipped."""
    if not isinstance(library, dict):
        return
    if ids is None:
        todo, explicit = sorted(k for k in library if isinstance(k, str)), False
    else:
        todo, explicit = ([ids] if isinstance(ids, str) else list(ids)), True
    seen = set()
    for mid in todo:
        if not isinstance(mid, str):
            continue
        entry = library.get(mid)
        if isinstance(entry, dict) and entry.get("type") == "multi_ep_alias":
            target = entry.get("alias_of")
            if not explicit or not isinstance(target, str):
                continue
            mid, entry = target, library.get(target)
        if not isinstance(entry, dict) or mid in seen:
            continue
        seen.add(mid)
        for obj in iter_entry_objects(mid, entry):
            if obj["cloud_state"] != "local":
                yield obj


def slot_owner(library, slot):
    """The dict an object's `slot` points at — the node that holds (or will hold) its `gphotos` block — or
    None when that path no longer exists. Accepts the tuple or its JSON list form."""
    node = library
    for step in slot if isinstance(slot, (list, tuple)) else ():
        if isinstance(node, dict) and isinstance(step, str):
            node = node.get(step)
        elif isinstance(node, list) and _is_int(step) and 0 <= step < len(node):
            node = node[step]
        else:
            return None
    return node if isinstance(node, dict) and node is not library else None


# ------------------------------------------------------------- capture reader (P-11)

def _capture_facts(obj):
    """One capture object (a prep `source` or a push object) -> its known, normalized facts."""
    facts = {"sha256": obj["sha256"]}
    for k in ("role", "uploaded_name", "local_name", "mtime_utc"):
        if _text(obj.get(k)):
            facts[k] = obj[k]
    for k in ("index", "size_bytes", "width", "height"):
        if _is_int(obj.get(k)) and obj[k] >= 0:
            facts[k] = obj[k]
    duration = obj.get("duration_s")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
        facts["duration_s"] = duration
    if isinstance(obj.get("date_utc"), str) and _DATE_UTC_RE.match(obj["date_utc"]):
        facts["date_utc"] = obj["date_utc"]
    if obj.get("approach_hint") in GP_APPROACHES:
        facts["approach_hint"] = obj["approach_hint"]
    sha1, key = obj.get("sha1"), gpweb.normalize_dedup_key(obj.get("dedup_key"))
    if isinstance(sha1, str) and _SHA1_RE.match(sha1):
        facts["sha1"] = sha1.lower()
        facts["dedup_key"] = gpweb.dedup_key(facts["sha1"])
    elif key:
        facts["sha1"] = gpweb.dedup_key_to_sha1_hex(key)
        facts["dedup_key"] = key
    return facts


def read_capture(folder, short_id):
    """The identity facts gpcapture recorded for the objects of one entry (or extras item) —
    `<folder>/<short_id>.gpcapture.json`, capture v1 (cb4dd91) or v2 (PLAN Step 5) — as {sha256: facts}.

    Sources in order, a later one winning key by key (a missing value never erases a known one): the prep
    record (the whole file: its `source` facts, the predicted `uploaded_name`, width / height from its tech
    scan), then every push in file order — a later push wins for the objects it carried, so the latest
    complete push wins and partial `chunk_range` pushes add theirs. facts: role (whole|chunk|holder), index,
    uploaded_name, local_name, size_bytes, mtime_utc, sha1 + dedup_key (both or neither), date_utc, width,
    height, duration_s, approach_hint (v2) and — ONLY for objects a push carried — `pushed` {at, device_id,
    device_tz, device_tz_offset_min: the ones known} plus remote_dir. An object without a SHA-256 cannot be
    matched to the library and is skipped; unknown keys are ignored (forward compatible). Capture facts are
    the push-time record, never proof of cloud presence (P-11). Never raises: absent / unreadable /
    malformed -> {}."""
    if not (_text(folder) and _text(short_id)):
        return {}
    try:
        with open(os.path.join(folder, f"{short_id}{_CAPTURE_SUFFIX}"), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    facts = {}

    def merge(new):
        facts.setdefault(new["sha256"], {}).update(new)

    prep = doc.get("prep")
    source = prep.get("source") if isinstance(prep, dict) else None
    if isinstance(source, dict) and _is_sha256(source.get("sha256")):
        whole = _capture_facts(source)
        whole["role"] = "whole"
        if _text(prep.get("uploaded_name")):
            whole["uploaded_name"] = prep["uploaded_name"]
        tech = prep.get("tech")
        size = _WIDTH_HEIGHT_RE.match(tech["width_height"]) if (
            isinstance(tech, dict) and isinstance(tech.get("width_height"), str)) else None
        if size:
            whole.setdefault("width", int(size.group(1)))
            whole.setdefault("height", int(size.group(2)))
        merge(whole)
    pushes = doc.get("pushes")
    for push in pushes if isinstance(pushes, list) else ():
        if not isinstance(push, dict):
            continue
        pushed = {}
        if _parse_at(push.get("captured_at")):
            pushed["at"] = push["captured_at"]
        for k in ("device_id", "device_tz"):
            if _text(push.get(k)):
                pushed[k] = push[k]
        if _is_int(push.get("device_tz_offset_min")):
            pushed["device_tz_offset_min"] = push["device_tz_offset_min"]
        objects = push.get("objects")
        for obj in objects if isinstance(objects, list) else ():
            if not (isinstance(obj, dict) and _is_sha256(obj.get("sha256"))):
                continue
            item = _capture_facts(obj)
            item["pushed"] = dict(pushed)
            if _text(push.get("remote_dir")):
                item["remote_dir"] = push["remote_dir"]
            merge(item)
    return facts


def unverified_objects(library, now):
    """The P-11 pending list, offline: every object a capture records as pushed that no trusted block confirms
    yet (cloud_state 'unverified') — the iter_uploaded_objects dicts plus `pushed_at` (the capture's '…Z' push
    time, or None) and `age_hours` (to `now`, an aware datetime — naive means UTC; None when the push time is
    unknown). Objects without a capture are the mapper's ('uploaded'), never pending. For `check_photos
    pending` and the auto-confirm after a push (PLAN Steps 13 / 13b)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    out = []
    for obj in iter_uploaded_objects(library):
        if obj["cloud_state"] != "unverified":
            continue
        pushed_at = obj["capture"]["pushed"].get("at")
        when = _parse_at(pushed_at)
        age = (now - when).total_seconds() / 3600.0 if when is not None else None
        out.append(dict(obj, pushed_at=pushed_at, age_hours=age))
    return out


# ------------------------------------------------------------- JSONL sidecars

def _append_jsonl(path, record):
    """Append `record` as ONE UTF-8 JSON line and fsync. An earlier torn line (a crash mid-append leaves no
    newline) is terminated first, so it can never swallow this record."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    with open(path, "a+b") as fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell() > 0:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                line = b"\n" + line
            fh.seek(0, os.SEEK_END)
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def _read_jsonl(path, normalize, errors):
    """[normalize(record)] for each line of a JSONL file, in file order; [] when the file does not exist. A line
    that is not JSON (a torn append) or that `normalize` rejects (ValueError) is skipped and reported to
    `errors` (a list, when given) as {path, line, error}, in line order. Any other OSError propagates — an
    unreadable sidecar must never look like an empty one."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return []
    out = []
    for lineno, raw in enumerate(data.split(b"\n"), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            value = json.loads(raw.decode("utf-8-sig"))  # -sig: a Windows editor may have added a BOM
        except (UnicodeDecodeError, ValueError):
            problem = "not a JSON line (torn write?)"
        else:
            try:
                out.append(normalize(value))
                continue
            except ValueError as e:
                problem = str(e)
        if errors is not None:
            errors.append({"path": path, "line": lineno, "error": problem})
    return out


# ------------------------------------------------------------- the learned store (P-10, P-5)

def learned_path(account):
    """~/.mediavault/gphotos/learned_<account>.jsonl — the per-account learned sidecar (P-10)."""
    if account not in GP_ACCOUNTS:
        raise ValueError(f"unknown Google account {account!r} (one of {GP_ACCOUNTS})")
    return os.path.join(GPHOTOS_STATE_DIR, f"learned_{account}.jsonl")


def _normalize_try(tried):
    """A learned record's `approach` {tried, outcome, n, via} -> validated and ordered."""
    if not isinstance(tried, dict):
        raise ValueError("learned record: approach must be a dict")
    unknown = sorted(str(k) for k in tried if k not in _TRY_KEYS)
    if unknown:
        raise ValueError(f"learned record: unknown approach key(s) {unknown}")
    t = {k: v for k, v in tried.items() if v is not None}
    if t.get("tried") not in GP_STAGES:
        raise ValueError(f"learned record: approach.tried must be one of {sorted(GP_STAGES)}")
    if t.get("outcome") not in GP_OUTCOMES:
        raise ValueError(f"learned record: approach.outcome must be one of {GP_OUTCOMES}")
    if "n" in t and not (_is_int(t["n"]) and t["n"] >= 0):
        raise ValueError("learned record: approach.n must be a non-negative integer")
    if "via" in t and t["via"] not in ("rpc", "ui"):
        raise ValueError("learned record: approach.via must be rpc or ui")
    return _ordered(t, _TRY_KEYS)


def _normalize_learned(record, account=None, fill=True):
    """Validate one learned record against the P-10 shape; -> its canonical form (None values dropped, keys
    in P-10 order, dedup_key canonical, alternates sorted). fill: stamp missing `v` / `at` / `account` (the
    write path) — reading requires them. Raises ValueError."""
    if not isinstance(record, dict):
        raise ValueError("learned record: expected a dict")
    unknown = sorted(str(k) for k in record if k not in _LEARNED_KEYS)
    if unknown:
        raise ValueError(f"learned record: unknown key(s) {unknown}")
    r = {k: v for k, v in record.items() if v is not None}
    if fill:
        r.setdefault("v", _LEARNED_VERSION)
        r.setdefault("at", _iso_now())
        if account is not None:
            r.setdefault("account", account)
    if not (_is_int(r.get("v")) and r["v"] == _LEARNED_VERSION):
        raise ValueError(f"learned record: v must be {_LEARNED_VERSION}")
    if _parse_at(r.get("at")) is None:
        raise ValueError("learned record: `at` must be an ISO-8601 UTC time ending in Z")
    if r.get("account") not in GP_ACCOUNTS:
        raise ValueError(f"learned record: account must be one of {GP_ACCOUNTS}")
    if account is not None and r["account"] != account:
        raise ValueError(f"learned record: account {r['account']!r} does not belong in the {account!r} sidecar")
    if not _text(r.get("manual_id")):
        raise ValueError("learned record: manual_id is required")
    if not _is_sha256(r.get("sha256")):
        raise ValueError("learned record: sha256 must be the object's 64-hex SHA-256")
    if r.get("source") not in GP_SOURCES:
        raise ValueError(f"learned record: source must be one of {GP_SOURCES}")
    if "label" in r and not isinstance(r["label"], str):
        raise ValueError("learned record: label must be a string")
    if "photo_id" in r and not (isinstance(r["photo_id"], str) and gpweb.MEDIA_KEY_RE.match(r["photo_id"])):
        raise ValueError("learned record: photo_id is not a Google Photos media key")
    for k in ("taken_ms", "tz_offset_ms", "upload_ms"):
        if k in r and not _is_int(r[k]):
            raise ValueError(f"learned record: {k} must be an integer")
    if "upload_ms" in r and r["upload_ms"] < 0:
        raise ValueError("learned record: upload_ms must not be negative")
    if "dedup_key" in r:
        key = gpweb.normalize_dedup_key(r["dedup_key"])
        if key is None:
            raise ValueError("learned record: dedup_key is not a dedupKey")
        r["dedup_key"] = key
    if "gp_filename" in r and not _text(r["gp_filename"]):
        raise ValueError("learned record: gp_filename must be a non-empty string")
    for k in ("width", "height"):
        if k in r and not (_is_int(r[k]) and r[k] >= 0):
            raise ValueError(f"learned record: {k} must be a non-negative integer")
    if "proof" in r and r["proof"] not in GP_PROOF_LADDER:
        raise ValueError(f"learned record: proof must be one of {GP_PROOF_LADDER}")
    if "approach" in r:
        r["approach"] = _normalize_try(r["approach"])
    if "stale" in r and not isinstance(r["stale"], bool):
        raise ValueError("learned record: stale must be true or false")
    if r.get("stale") and "photo_id" not in r:
        raise ValueError("learned record: a stale record names the dead photo_id")
    if "photo_id" in r and "proof" not in r and not r.get("stale"):
        raise ValueError("learned record: a photo_id needs its proof (P-5)")
    if "alternates" in r:
        alternates = r["alternates"]
        if not (isinstance(alternates, list)
                and all(isinstance(a, str) and gpweb.MEDIA_KEY_RE.match(a) for a in alternates)):
            raise ValueError("learned record: alternates must be a list of photo ids")
        r["alternates"] = sorted(set(alternates))
    for k in ("original_quality", "partial_upload"):
        if k in r and not isinstance(r[k], bool):
            raise ValueError(f"learned record: {k} must be true or false")
    if "search_override" in r:
        if not _text(r["search_override"]):
            raise ValueError("learned record: search_override must be a non-empty string")
        if r["source"] != "manual":
            raise ValueError("learned record: a search_override is the user's own term (source manual)")
        r["search_override"] = r["search_override"].strip()
    if not any(k in r for k in ("photo_id", "proof", "approach", "search_override", "alternates")):
        raise ValueError("learned record: carries no fact (photo_id, proof, approach, search_override or alternates)")
    return _ordered(r, _LEARNED_KEYS)


def append_learned(account, record):
    """Append one learned record — the P-10 line — to `account`'s sidecar: THE way every producer (map_photos,
    check_photos, fetch, confirm, resolve_photos) records a Google Photos fact; never write the file directly.
    Dry-run commands never call it (their results stay in their run directory until --apply).

    Keys (P-10 plus the fields P-1's account record needs): v · at · account · manual_id · label · sha256 ·
    photo_id · taken_ms · tz_offset_ms · upload_ms · dedup_key · gp_filename · width · height ·
    proof (GP_PROOF_LADDER) · source (GP_SOURCES) · approach {tried (GP_STAGES), outcome (GP_OUTCOMES), n,
    via rpc|ui} · stale · alternates · original_quality · partial_upload · search_override (source manual).
    Required: manual_id, sha256 (the OBJECT's current hash), source, and at least one of photo_id / proof /
    approach / search_override / alternates; a photo_id needs its proof unless the record declares it stale
    (dead). For proof sha256 the dedup_key must be the key of the verified BYTES
    (gpweb.dedup_key(cached_sha1(path))) — that is how a download teaches an object its content key.

    Unknown keys are refused, None values dropped; `v` (1), `at` (now) and `account` are filled; dedup_key is
    canonicalized, alternates sorted. The line is UTF-8 JSON with keys in P-10 order, appended and fsynced.
    Returns the record written. Raises ValueError for an invalid record (a producer bug) and OSError when the
    sidecar cannot be written — callers that must never fail (a running fetch) catch it themselves."""
    rec = _normalize_learned(record, account)
    _append_jsonl(learned_path(account), rec)
    return rec


def read_learned(account=None, errors=None):
    """The learned records of one account — or of every account, in GP_ACCOUNTS order — in append order.
    Torn-line tolerant: a line that is not JSON, or not a valid learned record of that sidecar, is skipped
    and, when `errors` is a list, reported there as {path, line, error}. [] for a missing sidecar."""
    out = []
    for acc in (GP_ACCOUNTS if account is None else (account,)):
        out.extend(_read_jsonl(learned_path(acc), lambda raw, acc=acc: _normalize_learned(raw, acc, fill=False),
                               errors))
    return out


def _reduce_group(account, sha256, group, last_stale, latest):
    """reduce_learned for one (account, sha256); `group` = [(ts, seq, record)], oldest first."""
    red = {"account": account, "sha256": sha256, "manual_id": group[-1][2]["manual_id"]}
    label = next((r["label"] for _ts, _seq, r in reversed(group) if "label" in r), None)
    if label is not None:
        red["label"] = label
    # The object's content key as a DOWNLOAD proved it (proof sha256: the verified bytes' key).
    content = next((r["dedup_key"] for _ts, _seq, r in reversed(group)
                    if r.get("proof") == "sha256" and "dedup_key" in r), None)

    def other_bytes(r):
        return content is not None and "dedup_key" in r and r["dedup_key"] != content

    no_stale = (float("-inf"), -1)
    candidates = [(ts, seq, r) for ts, seq, r in group
                  if "photo_id" in r and not r.get("stale") and not other_bytes(r)
                  and not (ts, seq) < last_stale.get((account, r["photo_id"]), no_stale)]
    if candidates:
        best = max(candidates, key=lambda t: (_strength(t[2]["proof"]), t[0], t[1]))[2]
        red["photo_id"] = best["photo_id"]
        same = [r for _ts, _seq, r in reversed(candidates) if r["photo_id"] == best["photo_id"]]
        for k in _ITEM_KEYS:  # facts about item P: the best record's, completed by newer records about P
            value = best[k] if k in best else next((r[k] for r in same if k in r), None)
            if value is not None:
                red[k] = value
        red.update(proof=best["proof"], source=best["source"], at=best["at"])
    else:
        proven = [(ts, seq, r) for ts, seq, r in group if "proof" in r and "photo_id" not in r]
        if proven:
            best = max(proven, key=lambda t: (_strength(t[2]["proof"]), t[0], t[1]))[2]
            red.update(proof=best["proof"], source=best["source"], at=best["at"])
    if content is not None:
        red["content_key"] = content

    verdicts = {}  # approach -> (at, outcome, via) of its newest verdict
    for _ts, _seq, r in group:
        tried = r.get("approach")
        if tried is None and r.get("stale"):
            tried = {"tried": "s0_photo_id", "outcome": "not-found"}  # a dead photo_id: direct access failed
        if tried is None or tried["outcome"] == "error":  # an error is no verdict on the approach
            continue
        verdicts[GP_STAGES[tried["tried"]]] = (r["at"], tried["outcome"], tried.get("via"))
    if verdicts:
        ok = [a for a in GP_APPROACHES if a in verdicts and verdicts[a][1] == "exactly-one"]
        failed = [a for a in GP_APPROACHES if a in verdicts and verdicts[a][1] != "exactly-one"]
        approach = {"best": ok[0], "ok": ok} if ok else {}
        if failed:
            approach["failed"] = failed
        approach["at"] = max((v[0] for v in verdicts.values()), key=_at_ts)
        if ok and verdicts[ok[0]][2]:
            approach["via"] = verdicts[ok[0]][2]
        red["approach"] = _ordered(approach, _APPROACH_KEYS)

    named = {r["photo_id"] for _ts, _seq, r in group if "photo_id" in r}
    for _ts, _seq, r in group:
        named.update(r.get("alternates", ()))
    dead = {p: latest[(account, p)]["at"] for p in sorted(named)
            if (account, p) in latest and latest[(account, p)].get("stale")}
    alternates = {r["photo_id"] for _ts, _seq, r in candidates}
    for _ts, _seq, r in group:
        alternates.update(r.get("alternates", ()))
    alternates -= {r["photo_id"] for _ts, _seq, r in group if "photo_id" in r and other_bytes(r)}
    alternates -= set(dead)
    alternates.discard(red.get("photo_id"))
    if alternates:
        red["alternates"] = sorted(alternates)
    if dead:
        red["dead"] = dead
    override = next(((r["search_override"], r["at"]) for _ts, _seq, r in reversed(group)
                     if "search_override" in r), None)
    if override:
        red["search_override"] = {"query": override[0], "at": override[1]}
    return _ordered(red, _REDUCED_KEYS)


def reduce_learned(records):
    """Reduce learned records to the best knowledge per object and account -> {(account, sha256): reduced}.

    Records are replayed in time order (`at`, then input order). Per (account, sha256):
      * identity — the best record naming a photo_id by the P-5 proof ladder (dedupkey > sha256 > filename >
        manual), then the newest; its item facts (taken_ms, tz_offset_ms, upload_ms, dedup_key, gp_filename,
        width, height, original_quality, partial_upload) completed from newer records about the same photo_id.
        Without any photo_id, the best bare proof (e.g. a download's sha256 match) is kept as proof/source/at;
      * stale — a stale record supersedes every OLDER record naming its photo_id in that account (whatever
        object they were about); a photo_id whose newest record is stale is `dead` {photo_id: at} and never an
        identity or an alternate; a stale record without its own approach counts as s0_photo_id not-found;
      * content_key — the newest proof-sha256 dedup_key (the verified bytes' key); a record whose item has
        another dedupKey is excluded from identity and alternates (proven other bytes);
      * approach — each approach's newest verdict (an `error` is no verdict): exactly-one -> ok, else failed;
        best = the first ok in waterfall order (GP_APPROACHES), via = how that verdict was reached;
      * alternates — every other live photo_id and every recorded alternate, minus dead / other-bytes ids;
      * search_override — the newest {query, at}.
    Invalid records are skipped. The reduced dict (keys: account, sha256, manual_id, label, photo_id + the item
    facts, proof, source, at, content_key, approach, alternates, dead, search_override — only those known) is
    what apply_learned folds into a block."""
    entries = []
    for seq, record in enumerate(records or ()):
        try:
            r = _normalize_learned(record, fill=False)
        except ValueError:
            continue
        entries.append((_at_ts(r["at"]), seq, r))
    entries.sort(key=lambda t: (t[0], t[1]))
    last_stale, latest, groups = {}, {}, {}
    for ts, seq, r in entries:
        if "photo_id" in r:
            key = (r["account"], r["photo_id"])
            latest[key] = r
            if r.get("stale"):
                last_stale[key] = (ts, seq)
        groups.setdefault((r["account"], r["sha256"]), []).append((ts, seq, r))
    return {key: _reduce_group(key[0], key[1], groups[key], last_stale, latest) for key in sorted(groups)}


def _merge_approach(held, incoming):
    """Merge two account-record `approach` dicts ({best, ok, failed, at, via}); per approach the verdict of the
    newer one wins. Approach names outside GP_APPROACHES are kept after the known ones."""
    def verdicts(approach):
        out = {}
        for verdict in ("failed", "ok"):
            names = approach.get(verdict)
            for name in names if isinstance(names, list) else ():
                if isinstance(name, str):
                    out[name] = verdict
        return out

    old, new = verdicts(held), verdicts(incoming)
    incoming_newer = _at_ts(incoming.get("at")) >= _at_ts(held.get("at"))
    merged = {**old, **new} if incoming_newer else {**new, **old}
    order = [a for a in GP_APPROACHES if a in merged] + sorted(a for a in merged if a not in GP_APPROACHES)
    ok = [a for a in order if merged[a] == "ok"]
    failed = [a for a in order if merged[a] == "failed"]
    out = {"best": ok[0], "ok": ok} if ok else {}
    if failed:
        out["failed"] = failed
    at = max((a for a in (held.get("at"), incoming.get("at")) if _parse_at(a)), key=_at_ts, default=None)
    if at:
        out["at"] = at
    if ok:
        source = incoming if ok[0] in new and (incoming_newer or ok[0] not in old) else held
        if source.get("via") in ("rpc", "ui"):
            out["via"] = source["via"]
    return _ordered(out, _APPROACH_KEYS)


def apply_learned(block, record, account):
    """Fold ONE reduced record (reduce_learned; a raw learned record is reduced first) into a `gphotos` block
    for `account` — PURE: the input block is never modified. Returns (new_block, changed, conflict).

    block None -> a new block pinned to the record's sha256 (the apply engine normally passes the block it
    built from the capture). A conflict returns the block UNCHANGED (changed False) with a code:
      invalid-record · account-mismatch · block-version (written by newer code — never overwritten) ·
      pin-mismatch (P-2: the block describes other bytes; the engine, which knows the object's current hash,
      replaces it — never this function) · malformed-block · content-key-mismatch (the block's content key and
      a download's disagree) · dedup-key-mismatch (the record's item — or, with no identity in the record, the
      account's current item — holds other bytes than the object's content key: a wrong item, reported).
    Otherwise, into accounts[account] (P-1 shape):
      * identity: a better photo_id (P-5 proof, then newer) replaces the current one, which becomes an
        alternate; a weaker one only joins the alternates; the same photo_id refreshes its item facts when the
        record is newer and keeps its strongest proof; a photo_id declared dead by a newer stale record, or
        whose item holds other bytes than the object, is dropped with its facts;
      * approach: verdicts merged per approach, the newer evidence winning; best = first ok in waterfall order;
      * alternates: union, minus the current photo_id and dead ids;
      * confirmed: True only for a photo_id with upload_ms, verified_by dedupkey / sha256 / filename and source
        map / check / fetch / confirm (never from a capture — P-11 — nor from a manual match alone);
      * a download's content key (proof sha256) fills the block's sha1 / dedup_key when they are unknown;
      * search_override (block level): the newer one wins.
    home_account is NOT touched: the engine stores home_account(...) (P-4) after folding. P-1 key order is
    kept; keys this code does not know are preserved."""
    red = record
    if isinstance(red, dict) and "v" in red:  # a raw learned record
        red = next(iter(reduce_learned([red]).values()), None)
    if not (isinstance(red, dict) and red.get("account") in GP_ACCOUNTS and _is_sha256(red.get("sha256"))):
        return block, False, "invalid-record"
    if red["account"] != account:
        return block, False, "account-mismatch"
    if block is None:
        new = new_gphotos_block(red["sha256"])
    else:
        version = block.get("v") if isinstance(block, dict) else None
        if _is_int(version) and version > GPHOTOS_BLOCK_VERSION:
            return block, False, "block-version"
        if not block_trusted(block, red["sha256"]):
            return block, False, "pin-mismatch"
        new = copy.deepcopy(block)
    accounts = new.get("accounts", {})
    if not isinstance(accounts, dict) or not isinstance(accounts.get(account, {}), dict):
        return block, False, "malformed-block"
    acc = copy.deepcopy(accounts.get(account, {}))

    block_key = gpweb.normalize_dedup_key(new.get("dedup_key"))
    if block_key is None and isinstance(new.get("sha1"), str) and _SHA1_RE.match(new["sha1"]):
        block_key = gpweb.dedup_key(new["sha1"].lower())
    learned_key = red.get("content_key")
    if block_key and learned_key and block_key != learned_key:
        return block, False, "content-key-mismatch"
    content = block_key or learned_key
    photo_id = red.get("photo_id")
    if content and photo_id and red.get("dedup_key") not in (None, content):
        return block, False, "dedup-key-mismatch"
    held_key = gpweb.normalize_dedup_key(acc.get("dedup_key"))
    held_wrong = bool(content and acc.get("photo_id") and held_key not in (None, content))
    if held_wrong and not photo_id:
        return block, False, "dedup-key-mismatch"
    if learned_key and not block_key:
        new["sha1"], new["dedup_key"] = gpweb.dedup_key_to_sha1_hex(learned_key), learned_key

    dead = red.get("dead") or {}
    current = acc.get("photo_id")
    if current and (held_wrong or (current in dead and _at_ts(dead[current]) >= _at_ts(acc.get("verified_at")))):
        for k in ("photo_id",) + _ITEM_KEYS + ("verified_at", "verified_by", "source", "confirmed"):
            acc.pop(k, None)
    held_alternates = acc.get("alternates")
    alternates = {a for a in held_alternates if isinstance(a, str)} if isinstance(held_alternates, list) else set()
    incoming = (_strength(red.get("proof")), _at_ts(red.get("at")))
    held = (_strength(acc.get("verified_by")), _at_ts(acc.get("verified_at")))
    if photo_id:
        if acc.get("photo_id") == photo_id:
            newer = incoming[1] >= held[1]
            for k in _ITEM_KEYS:
                if k in red and (newer or k not in acc):
                    acc[k] = red[k]
            if incoming >= held:
                acc.update(verified_at=red["at"], verified_by=red["proof"], source=red["source"])
        elif not acc.get("photo_id") or incoming > held:
            if acc.get("photo_id"):
                alternates.add(acc["photo_id"])
            for k in _ITEM_KEYS:
                acc.pop(k, None)
            acc["photo_id"] = photo_id
            acc.update({k: red[k] for k in _ITEM_KEYS if k in red})
            acc.update(verified_at=red["at"], verified_by=red["proof"], source=red["source"])
        else:
            alternates.add(photo_id)
    elif red.get("proof") and not acc.get("photo_id") and incoming >= held:
        acc.update(verified_at=red["at"], verified_by=red["proof"], source=red["source"])
    if "approach" in red:
        held_approach = acc.get("approach") if isinstance(acc.get("approach"), dict) else {}
        merged = _merge_approach(held_approach, red["approach"])
        if merged:
            acc["approach"] = merged
    alternates.update(red.get("alternates", ()))
    alternates -= set(dead)
    alternates.discard(acc.get("photo_id"))
    if alternates:
        acc["alternates"] = sorted(alternates)
    else:
        acc.pop("alternates", None)
    if (acc.get("photo_id") and _is_int(acc.get("upload_ms")) and acc.get("verified_by") in _VERIFIED_PROOFS
            and acc.get("source") in _CONFIRMING_SOURCES):
        acc["confirmed"] = True
    else:
        acc.pop("confirmed", None)

    accounts = dict(accounts)
    if acc:
        accounts[account] = _ordered(acc, _ACCOUNT_KEYS)
    else:
        accounts.pop(account, None)
    if accounts:
        order = [a for a in GP_ACCOUNTS if a in accounts] + [a for a in accounts if a not in GP_ACCOUNTS]
        new["accounts"] = {a: accounts[a] for a in order}
    else:
        new.pop("accounts", None)
    override = red.get("search_override")
    held_override = new.get("search_override")
    if override and (not isinstance(held_override, dict)
                     or _at_ts(override["at"]) >= _at_ts(held_override.get("at"))):
        new["search_override"] = {"query": override["query"], "at": override["at"]}
    new = _ordered(new, _BLOCK_KEYS)
    return new, (block is None or new != block), None


# ------------------------------------------------------------- the failed list (P-18)

def failed_path():
    """~/.mediavault/gphotos/failed.jsonl — the append-only failed list (P-18)."""
    return os.path.join(GPHOTOS_STATE_DIR, "failed.jsonl")


def failed_csv_path():
    """~/.mediavault/gphotos/failed_objects.csv — the failed list's regenerated Excel view (P-18)."""
    return os.path.join(GPHOTOS_STATE_DIR, "failed_objects.csv")


def _normalize_failed(record, fill=True):
    """Validate one failed-list record against the P-18 shape; -> its canonical form. fill: stamp a missing
    `at` and derive `category` from the id (the write path). Raises ValueError."""
    if not isinstance(record, dict):
        raise ValueError("failed record: expected a dict")
    unknown = sorted(str(k) for k in record if k not in _FAILED_KEYS)
    if unknown:
        raise ValueError(f"failed record: unknown key(s) {unknown}")
    r = {k: v for k, v in record.items() if v is not None}
    if fill:
        r.setdefault("at", _iso_now())
        if "category" not in r and category_for_id(r.get("manual_id")):
            r["category"] = category_for_id(r["manual_id"])
    if _parse_at(r.get("at")) is None:
        raise ValueError("failed record: `at` must be an ISO-8601 UTC time ending in Z")
    if r.get("account") not in GP_ACCOUNTS:
        raise ValueError(f"failed record: account must be one of {GP_ACCOUNTS}")
    if not _text(r.get("manual_id")):
        raise ValueError("failed record: manual_id is required")
    if r.get("reason") not in GP_FAILED_REASONS:
        raise ValueError(f"failed record: reason must be one of {GP_FAILED_REASONS}")
    if "category" in r and r["category"] not in GP_ACCOUNTS:
        raise ValueError(f"failed record: category must be one of {GP_ACCOUNTS}")
    for k in ("run", "label", "uploaded_name", "expected_taken_local"):
        if k in r and not isinstance(r[k], str):
            raise ValueError(f"failed record: {k} must be a string")
    if "sha256" in r and not _is_sha256(r["sha256"]):
        raise ValueError("failed record: sha256 must be the object's 64-hex SHA-256")
    if "tried" in r:
        if not isinstance(r["tried"], list):
            raise ValueError("failed record: tried must be a list of {approach, outcome, n}")
        tried = []
        for t in r["tried"]:
            if not isinstance(t, dict) or any(k not in _TRIED_KEYS for k in t):
                raise ValueError("failed record: tried entries are {approach, outcome, n}")
            t = {k: v for k, v in t.items() if v is not None}
            if t.get("approach") not in GP_APPROACHES:
                raise ValueError(f"failed record: tried.approach must be one of {GP_APPROACHES}")
            if t.get("outcome") not in GP_OUTCOMES:
                raise ValueError(f"failed record: tried.outcome must be one of {GP_OUTCOMES}")
            if "n" in t and not (_is_int(t["n"]) and t["n"] >= 0):
                raise ValueError("failed record: tried.n must be a non-negative integer")
            tried.append(_ordered(t, _TRIED_KEYS))
        r["tried"] = tried
    if "candidates" in r:
        candidates = r["candidates"]
        if not (isinstance(candidates, list)
                and all(isinstance(p, str) and gpweb.MEDIA_KEY_RE.match(p) for p in candidates)):
            raise ValueError("failed record: candidates must be a list of photo ids")
        r["candidates"] = list(dict.fromkeys(candidates))
    return _ordered(r, _FAILED_KEYS)


def append_failed(record):
    """Append one object to the failed list (P-18) — THE way every producer (map_photos, fetch, check_photos,
    the auto-confirm) records an object it could not pin to exactly one verified item.

    Shape: at · run · account · category · manual_id · label · sha256 · uploaded_name · expected_taken_local ·
    tried [{approach (GP_APPROACHES), outcome (GP_OUTCOMES), n}] · candidates [photo ids seen — resolve_photos
    shows them; exactly one becomes the CSV's suggested_photo_url] · reason (GP_FAILED_REASONS). Required:
    account, manual_id, reason; `at` (now) and `category` (from the id) are filled. Unknown keys are refused,
    None values dropped. One UTF-8 JSON line, appended and fsynced. Returns the record written; raises
    ValueError (invalid record) / OSError (cannot write)."""
    rec = _normalize_failed(record)
    _append_jsonl(failed_path(), rec)
    return rec


def read_failed(errors=None):
    """The failed list in append order. Torn-line tolerant exactly like read_learned (skipped lines are
    reported to `errors` when it is a list). [] when there is no failed list."""
    return _read_jsonl(failed_path(), lambda raw: _normalize_failed(raw, fill=False), errors)


def write_failed_csv(path, rows):
    """Write `rows` (dicts keyed by GP_FAILED_CSV_COLUMNS; a missing key is an empty cell) as the Excel-friendly
    CSV P-18 specifies — UTF-8 with BOM, CRLF line ends, the GP_FAILED_CSV_COLUMNS header — atomically (temp
    file + os.replace). Shared by export_failed_csv and map_photos' resolutions_todo.csv (PLAN Step 9), which
    read_failed_csv reads back. Raises OSError when the file cannot be replaced (e.g. it is open in Excel)."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".failed.", suffix=".csv.tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, lineterminator="\r\n")
            writer.writerow(GP_FAILED_CSV_COLUMNS)
            for row in rows:
                writer.writerow(["" if row.get(c) is None else str(row.get(c)) for c in GP_FAILED_CSV_COLUMNS])
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _failed_row(rec, obj):
    """One failed_objects.csv row for a failed record and its (unconfirmed) library object."""
    tried = "; ".join(f"{t['approach']}={t['outcome']}" + (f"({t['n']})" if "n" in t else "")
                      for t in rec.get("tried", ()))
    candidates = rec.get("candidates", [])
    if candidates:
        tried = (tried + " | " if tried else "") + "candidates: " + " ".join(candidates)
    extra = obj["kind"] in ("extra_whole", "extra_chunk")
    return {"account": rec["account"], "category": rec.get("category") or obj["category"],
            "manual_id": rec["manual_id"], "object": rec.get("label") or obj["label"],
            "uploaded_name": rec.get("uploaded_name") or obj["uploaded_name"], "status": rec["reason"],
            "candidates": tried,
            "suggested_photo_url": _PHOTO_URL + candidates[0] if len(candidates) == 1 else "",
            "expected_taken_local": rec.get("expected_taken_local"),
            # The existing entry-level fix (cmd_set_search); an extra's term goes in new_search_term instead.
            "set_search_cmd": "" if extra else f'python main.py set_search {rec["manual_id"]} "<new search term>"',
            "sha256": rec.get("sha256") or obj["sha256"]}


def export_failed_csv(library):
    """Regenerate failed_objects.csv (P-18) from the failed list against `library`: one row per object — each
    record is matched to its object by (manual_id, sha256), or by its label when it has no hash, and the
    newest record per object wins — sorted by account, id and the object's position in its entry. Rows whose
    object is now confirmed (a trusted block with a confirmed account record) disappear, as do records whose
    object no longer exists (re-split, re-mastered, removed — nothing left to fix; failed.jsonl keeps them).
    The editable columns (decision, photo_url, date, time, gmt, new_search_term, note) start empty;
    `set_search_cmd` carries the ready-made `set_search` command. Returns {path, rows, dropped_confirmed,
    dropped_orphans} (orphans counted per distinct missing object key). Raises OSError when the CSV cannot be
    written (e.g. open in Excel) — the failed list itself is never touched."""
    records = read_failed()
    objects = {}
    for mid in sorted({rec["manual_id"] for rec in records}):
        entry = library.get(mid) if isinstance(library, dict) else None
        for pos, obj in enumerate(iter_entry_objects(mid, entry)):
            objects.setdefault((mid, obj["sha256"]), (pos, obj))
            objects.setdefault((mid, "label:" + obj["label"]), (pos, obj))
    newest, orphans = {}, set()
    for seq, rec in enumerate(records):
        key = (rec["manual_id"], rec.get("sha256") or "label:" + rec.get("label", ""))
        hit = objects.get(key)
        if hit is None:
            orphans.add(key)
            continue
        pos, obj = hit
        rank = (_at_ts(rec["at"]), seq)
        if obj["slot"] not in newest or rank >= newest[obj["slot"]][0]:
            newest[obj["slot"]] = (rank, rec, pos, obj)
    rows, confirmed = [], 0
    for _rank, rec, pos, obj in newest.values():
        if obj["cloud_state"] == "confirmed":
            confirmed += 1
            continue
        rows.append(((rec["account"], rec["manual_id"], pos), _failed_row(rec, obj)))
    rows.sort(key=lambda t: t[0])
    path = failed_csv_path()
    write_failed_csv(path, [row for _key, row in rows])
    return {"path": path, "rows": len(rows), "dropped_confirmed": confirmed, "dropped_orphans": len(orphans)}


def _csv_header(cell):
    """A header cell -> its column name: BOM and a '(hint)' suffix removed, trimmed, lower-case, spaces -> _."""
    name = (cell or "").replace("\N{ZERO WIDTH NO-BREAK SPACE}", "").split("(")[0].strip().lower()
    return re.sub(r"\s+", "_", name)


def _csv_date(text):
    """A date cell -> date. ISO first (YYYY-MM-DD, `/` or `.` also; an Excel time part ignored), then month
    names (5 Feb 2022, Feb 5, 2022, 21 Sept 2026), then a numeric day/month/year ONLY when it cannot be misread
    (a part above 12, or day == month): Excel re-writes a typed date in the locale's short format, and
    05/02/2022 could be either. Raises ValueError saying how to fix it."""
    s = " ".join(text.replace(",", " ").split())
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?$", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    named = re.sub(r"(?i)\bsept\b", "Sep", s)
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(named, fmt).date()
        except ValueError:
            pass
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a == b or a > 12 or b > 12:
            day, month = (b, a) if b > 12 else (a, b)
            return datetime(year, month, day).date()
        raise ValueError(f"date {text!r} is ambiguous (day/month or month/day?) — write it as YYYY-MM-DD "
                         "(format the column as Text in Excel)")
    raise ValueError(f"date {text!r} is not a date — write it as YYYY-MM-DD")


def _csv_time(text):
    """A time cell -> time: HH:MM[:SS] (24 h) or h:MM[:SS] AM/PM (a. m. / p.m. and no-break spaces tolerated)."""
    s = " ".join(text.replace("\N{NARROW NO-BREAK SPACE}", " ").replace("\N{NO-BREAK SPACE}", " ").split())
    s = re.sub(r"(?i)\s*([ap])\.?\s*m\.?$", lambda m: " " + m.group(1).upper() + "M", s)
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    raise ValueError(f"time {text!r} is not a time — use HH:MM[:SS] (24 h) or h:MM[:SS] AM/PM")


def _csv_edit(cell, decision, line):
    """One edited CSV row (header -> value, stripped) -> the validated edit dict; raises ValueError."""
    if decision not in GP_DECISIONS:
        raise ValueError(f"decision {cell.get('decision')!r} is not one of {', '.join(GP_DECISIONS)}")
    manual_id = cell.get("manual_id", "")
    if not manual_id:
        raise ValueError("manual_id is empty")
    account = cell.get("account", "").lower()
    if account not in GP_ACCOUNTS:
        raise ValueError(f"account {cell.get('account', '')!r} is not one of {', '.join(GP_ACCOUNTS)}")
    row = {"line": line, "account": account, "manual_id": manual_id, "decision": decision}
    for key in ("category", "object", "uploaded_name", "note"):
        if cell.get(key):
            row[key] = cell[key]
    if cell.get("sha256"):
        if not _is_sha256(cell["sha256"]):
            raise ValueError("the sha256 column was changed — it must stay the object's 64-hex SHA-256")
        row["sha256"] = cell["sha256"]
    if decision == "url":
        url = cell.get("photo_url", "")
        photo_id = gpweb.photo_id_from_url(url) or (url if gpweb.MEDIA_KEY_RE.match(url) else None)
        if not photo_id:
            raise ValueError("decision url needs photo_url: the item's Google Photos link (…/photo/<id>)")
        row["photo_id"] = photo_id
    elif decision == "datetime":
        if not (cell.get("date") and cell.get("time") and cell.get("gmt")):
            raise ValueError("decision datetime needs date, time and gmt (e.g. 2022-02-05, 19:49:46, +08:00)")
        local = datetime.combine(_csv_date(cell["date"]), _csv_time(cell["time"]))
        offset = gpweb.gmt_offset_minutes(cell["gmt"])
        if offset is None:
            raise ValueError(f"gmt {cell['gmt']!r} is not a UTC offset — use +08:00 (or GMT+08:00)")
        row["taken_local"] = local.strftime("%Y-%m-%dT%H:%M:%S")
        row["gmt_offset_min"] = offset
        row["taken_ms"] = int((local - datetime(1970, 1, 1)).total_seconds()) * 1000 - offset * 60_000
    elif decision == "search":
        if not cell.get("new_search_term"):
            raise ValueError("decision search needs new_search_term")
        row["search_term"] = cell["new_search_term"]
    return row


def read_failed_csv(path):
    """The user's edits in failed_objects.csv or resolutions_todo.csv -> (rows, errors), for map_photos
    --resolutions (PLAN Step 9) and resolve_photos (Step 14).

    Excel-tolerant: UTF-8 with or without BOM (else the ANSI code page), CRLF or LF, a `sep=` first line, `;`
    or tab instead of `,`, headers in any case / order / with a `(hint)` suffix, extra columns, blank rows.
    Only rows with a `decision` are edits. A valid edit -> {line, account, manual_id, decision, and the
    category / object / uploaded_name / note / sha256 present} plus, per decision: url -> photo_id;
    datetime -> taken_local ('YYYY-MM-DDTHH:MM:SS'), gmt_offset_min, taken_ms (UTC epoch ms); search ->
    search_term; not_uploaded / skip -> nothing more. An invalid edit goes to `errors` as {line, manual_id,
    error} — never into `rows`. Raises ValueError when the file lacks the account / manual_id / decision
    columns, OSError when it cannot be read."""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    end = text.find("\n")
    first = (text if end < 0 else text[:end]).strip("\r")
    delimiter = ","
    if first.lower().startswith("sep=") and len(first) >= 5:
        delimiter, text = first[4], ("" if end < 0 else text[end + 1:])
    else:
        counts = {d: first.count(d) for d in (",", ";", "\t")}
        if max(counts.values()) > 0:
            delimiter = max(counts, key=counts.get)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = next(reader, None)
    columns = [_csv_header(h) for h in (header or [])]
    missing = [c for c in ("account", "manual_id", "decision") if c not in columns]
    if missing:
        raise ValueError(f"{os.path.basename(path)} is not a failed-objects / resolutions CSV: "
                         f"missing column(s) {', '.join(missing)}")
    rows, errors = [], []
    for cells in reader:
        cell = {}
        for name, value in zip(columns, cells):
            if name and name not in cell:
                cell[name] = value.strip()
        decision = cell.get("decision", "").lower()
        if not decision:
            continue  # not edited (or a blank line)
        try:
            rows.append(_csv_edit(cell, decision, reader.line_num))
        except ValueError as e:
            errors.append({"line": reader.line_num, "manual_id": cell.get("manual_id", ""), "error": str(e)})
    return rows, errors
