"""
One-time, metadata-only, idempotent migration: stamps ``re_hashed=False`` onto every
existing split entry (``split_info.is_split is True``) that does not yet carry the
``re_hashed`` key, so the library is schema-consistent after the split-hash-deterministic
feature lands.  Non-split entries and ``season_map`` parents are left completely
untouched.  The function is safe to re-run; a second call that changes nothing does not
write to disk.

Never touch real C:\\Media files or real library_*.json when TESTING — the unit test
runs this against the ``sandbox`` libraries; the real run is a manual command the user
invokes.
"""

from mvcommon import load_library, save_library


def migrate(verbose=True) -> dict:
    """Stamp ``re_hashed=False`` on every un-flagged split entry.

    Parameters
    ----------
    verbose : bool
        When True, prints a summary line after processing.

    Returns
    -------
    dict
        Keys: ``scanned``, ``stamped``, ``already_had_flag``, ``skipped_non_split``.
        ``scanned`` counts every entry considered; the other keys are mutually
        exclusive and sum to ``scanned``.
    """
    data = load_library()

    counts = {
        "scanned": 0,
        "stamped": 0,
        "already_had_flag": 0,
        "skipped_non_split": 0,
    }

    for _key, entry in data.items():
        # Skip season_map parents — they are containers, not leaf media entries.
        if entry.get("type") == "season_map":
            continue

        counts["scanned"] += 1

        split_info = entry.get("split_info", {})
        is_split = split_info.get("is_split") is True

        if not is_split:
            counts["skipped_non_split"] += 1
            continue

        # is_split == True from here on.
        if "re_hashed" in entry:
            counts["already_had_flag"] += 1
            continue

        # Missing flag on a split entry — stamp it.
        entry["re_hashed"] = False
        counts["stamped"] += 1

    # Only write if at least one entry was changed (idempotent + safe).
    if counts["stamped"] > 0:
        save_library(data)

    if verbose:
        print(
            f"migrate_rehash_flag: scanned={counts['scanned']}  "
            f"stamped={counts['stamped']}  "
            f"already_had_flag={counts['already_had_flag']}  "
            f"skipped_non_split={counts['skipped_non_split']}"
        )

    return counts


if __name__ == "__main__":
    migrate()
