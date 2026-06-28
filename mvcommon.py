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
from datetime import datetime, timezone
from subprocess import SubprocessError

# ==========================================
#         SHARED CONFIGURATION
# ==========================================
# Single source of truth for the library path/folder constants imported by
# both main.py and mainfetch.py. Importing only stdlib keeps this module free
# of any import-cycle risk (it must NEVER import main or mainfetch).
LIBRARY_MOVIES = r'C:\Media\library_movies.json'
LIBRARY_SERIES = r'C:\Media\library_series.json'
LIBRARY_ANIME = r'C:\Media\library_anime.json'
LIBRARY_OTHERS = r'C:\Media\library_others.json'

LOCAL_ROOT = r"C:\Media"  # Your PC Root
MKVMERGE_PATH = r"C:\Program Files\MKVToolNix\mkvmerge.exe"

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
    """Split the merged library dict back into its 4 per-prefix files.

    Routing is table-driven (see ``_PREFIX_TO_LIB`` below): each key goes to the
    FIRST prefix it matches —
        mov… -> library_movies.json
        tv…  -> library_series.json
        ani… -> library_anime.json
        oth… -> library_others.json   (IMP-D18)
    Both the per-file buckets AND the atomic-write set are derived from that one
    table, so adding the next category is a single-line edit there — there is no
    second list to keep in sync.

    A key matching NO known prefix is still routed to Movies for backward
    compatibility (legacy / no-prefix ids), but the routing is WARNED on stderr —
    never silent — so an unknown prefix can never vanish into library_movies.json
    undetected.
    """
    # Ordered prefix -> destination-file routing table. FIRST match wins, so this
    # order reproduces the historical mov/tv/ani if-chain with "oth" (IMP-D18)
    # appended. Kept FUNCTION-LOCAL on purpose: the tests redirect library I/O by
    # monkeypatching mvcommon.LIBRARY_* AFTER import (the `sandbox` fixture), so the
    # table must read the CURRENT module globals at call time — a module-level table
    # would freeze the real C:\Media paths at import and defeat the sandbox redirect.
    _PREFIX_TO_LIB = [
        ("mov", LIBRARY_MOVIES),
        ("tv", LIBRARY_SERIES),
        ("ani", LIBRARY_ANIME),
        ("oth", LIBRARY_OTHERS),
    ]
    # Legacy / unknown-prefix keys fall back to Movies for back-compat. This is the
    # SAME bucket as the "mov" entry above, so they share library_movies.json
    # exactly as before — only now the fallback is explicit and warned, not silent.
    fallback_path = LIBRARY_MOVIES

    # One bucket per destination file, keyed by path and derived from the table so
    # the set of output files always matches _PREFIX_TO_LIB. Insertion order = table
    # order, preserved by the write loop below (movies, series, anime, others).
    buckets = {path: {} for _, path in _PREFIX_TO_LIB}

    for key, val in data.items():
        for prefix, path in _PREFIX_TO_LIB:
            if key.startswith(prefix):
                buckets[path][key] = val
                break
        else:
            # No known prefix. Keep back-compat (route to Movies) but WARN loudly,
            # ONCE per key, so a future unknown prefix can never silently vanish.
            known = "/".join(prefix for prefix, _ in _PREFIX_TO_LIB)
            print(
                f"⚠️  save_library: key '{key}' has no known prefix ({known}) — "
                f"routing to {os.path.basename(fallback_path)} (legacy fallback).",
                file=sys.stderr,
            )
            buckets[fallback_path][key] = val

    # Atomic write: write to a temp file then os.replace() to prevent
    # partial-write corruption if the process is killed mid-save. The file set is
    # derived from the same table (buckets), so it stays in lock-step with routing.
    for path, content in buckets.items():
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
        with open(filepath, 'rb') as f:
            for block in iter(lambda: f.read(block_size), b''):
                sha256.update(block)
                done += len(block)
                pct = done / total if total else 1.0
                filled = int(bar_width * pct)
                bar = '█' * filled + '░' * (bar_width - filled)
                size_str = f"{human_readable_size(done)} / {human_readable_size(total)}"
                print(f"\r  🔍 {fname}  [{bar}] {size_str} ", end='', flush=True)
        print()
        return sha256.hexdigest()
    except Exception as e:
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
