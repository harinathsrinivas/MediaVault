"""Daily Google-Photos session keep-alive runner (IMP-C17).

Opens each persistent Chrome profile (movies / tv / anime), navigates to
photos.google.com, and confirms the Google session is still signed in via the
shared detector ``mainfetch.check_session_alive``. Keeping the cookies warm on a
daily cadence reduces forced re-auth, so an unattended/couch fetch does not die
silently on an expired session (IMP-C6).

Invoked by Windows Task Scheduler (see ``tools/mediavault_warm_profiles.xml``)
once a day when the machine is idle, and runnable by hand:

    python tools/warm_profiles.py            # warm all profiles
    python tools/warm_profiles.py --profile anime   # warm just one

A single-flight lock (``mvcommon.fetch_session_lock``) keeps the warm-up from
colliding with a live fetch on Chrome's fixed CDP port 9222: if a live fetch
holds the lock, the warm-up skips and exits 0. Each run appends one structured
line to ``~/.mediavault/logs/warm_profiles.log``. If any profile is logged out
or fails to launch, the run prints a summary, fires a desktop toast, and exits
non-zero so the failure is loud.
"""

import os
import sys
import time
import argparse
from datetime import datetime

# Allow running both as `python tools/warm_profiles.py` and as `import tools.warm_profiles`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import mainfetch
import mvcommon
from mvcommon import fetch_session_lock, LockHeldError
from tools.notify_toast import send_toast

# Per-run log. Module-level so tests can monkeypatch it to a tmp file.
LOG_PATH = os.path.join(mvcommon.MV_LOG_DIR, "warm_profiles.log")


def _append_log(results):
    """Append ONE structured line per run: timestamp + per-profile key=STATUS.

    ``results`` is an ordered dict-like mapping profile key -> status string.
    A logging failure must never crash the run, so the file write is guarded.
    Reads the module global LOG_PATH at call time (so tests can redirect it).
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pairs = " ".join(f"{key}={status}" for key, status in results.items())
    line = f"{stamp} {pairs}\n"
    try:
        os.makedirs(mvcommon.MV_LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        # Logging is best-effort; surface it but never abort the run.
        print(f"   > ⚠️  Could not write warm-up log ({exc}).")


def _warm_one(key):
    """Warm a single profile and return its status string.

    "OK"          — session confirmed alive.
    "LOGGED_OUT"  — navigated, but the session is signed out (re-login needed).
    "LAUNCH_FAIL" — Chrome/Selenium failed to launch or attach.

    The driver is ALWAYS quit in a finally (the quit itself is guarded — it can
    raise if the browser already died).
    """
    driver = mainfetch.init_driver(key)
    if driver is None:
        return "LAUNCH_FAIL"

    try:
        driver.get(mainfetch.PHOTOS_URL)
        time.sleep(2)
        mainfetch.check_session_alive(driver, key)
        return "OK"
    except mainfetch.SessionExpiredError:
        return "LOGGED_OUT"
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def warm_all(profile_keys=None):
    """Warm the given profiles (default: all) and return the process exit code.

    0 = healthy (or skipped because a live fetch holds the lock);
    1 = at least one profile is logged out or failed to launch.
    """
    if profile_keys is None:
        profile_keys = list(mainfetch.CHROME_PROFILES.keys())

    # Single-flight: never collide with a live fetch on CDP port 9222. The lock
    # is held ONLY for the browser work; LockHeldError is raised on __enter__,
    # so catch it around the whole `with` to take the skip path.
    results = {}
    try:
        with fetch_session_lock(blocking=False):
            for key in profile_keys:
                print(f"   > Warming profile '{key}'...")
                results[key] = _warm_one(key)
    except LockHeldError:
        print("⏭️ Live fetch in progress — skipping warm-up.")
        _append_log({"_run": "SKIPPED_LOCK_HELD"})
        return 0

    _append_log(results)

    failed = [key for key, status in results.items()
              if status in ("LOGGED_OUT", "LAUNCH_FAIL")]
    if failed:
        print("⚠️  Profiles needing attention:")
        for key in profile_keys:
            print(f"   {key}: {results[key]}")
        send_toast(
            "MediaVault: account needs attention",
            f"{', '.join(failed)} logged out — re-login required",
        )
        return 1

    print("✅ All profiles healthy.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Keep Google-Photos Chrome profiles signed in (IMP-C17)."
    )
    parser.add_argument(
        "--profile",
        metavar="KEY",
        help="Warm only this profile (one of: "
             + ", ".join(mainfetch.CHROME_PROFILES.keys()) + ").",
    )
    args = parser.parse_args(argv)

    profile_keys = None
    if args.profile is not None:
        if args.profile not in mainfetch.CHROME_PROFILES:
            valid = ", ".join(mainfetch.CHROME_PROFILES.keys())
            print(
                f"❌ Unknown profile '{args.profile}'. Valid profiles: {valid}.",
                file=sys.stderr,
            )
            sys.exit(2)
        profile_keys = [args.profile]

    return warm_all(profile_keys)


if __name__ == "__main__":
    sys.exit(main())
