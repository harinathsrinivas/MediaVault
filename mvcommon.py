import os
import json
import sys
import hashlib
import re
import tempfile
import time
import random
import contextlib
import errno
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
# settings the source tree must not hard-code (the web bind host/port, the
# shared web auth token, the TMDB API key). It is read ONCE and cached.
#
# Contract (see mvconfig.example.json):
#   {"web": {"host": "127.0.0.1", "port": 8765, "token": null},
#    "tmdb": {"api_key": null}}
#
# Resolution rules:
#   * An ABSENT file, or an absent key, falls back to the documented default
#     (host 127.0.0.1, port 8765, NO token -> no auth) — today's behaviour, so
#     local dev and the test suite stay frictionless with no config file.
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


def web_token():
    """Configured shared web auth token, or "" when no token is set.

    Returns "" (falsy) for absent / null / blank / non-string so callers can
    test ``if web_token():`` to decide whether auth is enabled. A non-empty
    string is returned stripped of surrounding whitespace."""
    token = _config_section("web").get("token")
    if isinstance(token, str) and token.strip():
        return token.strip()
    return ""


def tmdb_api_key():
    """Configured TMDB API key, or "" when none is set.

    Returns "" (falsy) for absent / null / blank / non-string."""
    key = _config_section("tmdb").get("api_key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    return ""


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

    for path in [LIBRARY_MOVIES, LIBRARY_SERIES, LIBRARY_ANIME]:
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
    """Splits the merged dictionary back into 3 files based on prefix."""
    mov_data = {}
    tv_data = {}
    ani_data = {}

    for key, val in data.items():
        if key.startswith("mov"):
            mov_data[key] = val
        elif key.startswith("tv"):
            tv_data[key] = val
        elif key.startswith("ani"):
            ani_data[key] = val
        else:
            # Fallback for legacy/unknown keys -> Movies
            mov_data[key] = val

    # Atomic write: write to a temp file then os.replace() to prevent
    # partial-write corruption if the process is killed mid-save.
    for path, content in [(LIBRARY_MOVIES, mov_data), (LIBRARY_SERIES, tv_data), (LIBRARY_ANIME, ani_data)]:
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
