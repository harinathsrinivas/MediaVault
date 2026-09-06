"""One-shot migration: provider folder tokens `{tmdb-…}` → `[tmdbid-…]` (IMP-U6).

Converts every media folder whose leaf name carries the LEGACY curly token
(`Movie (2020) {tmdb-59436}` / `{TMDB-69590}` — pre-IMP-U6 naming) to the
canonical Jellyfin/Emby-native square form (`Movie (2020) [tmdbid-59436]`),
via MediaVault's own crash-safe `cmd_rename_folder` (journal-backed, hash-safe,
rewrites `folder_path` for every descendant). In the SAME pass it writes the
`movie.nfo` / `tvshow.nfo` id sidecar (D6) so Plex's NFO agent pins the same id.

Shape rules:
  * every `{tmdb-<id>}` / `{TMDB-<id>}`  -> `[tmdbid-<id>]` (same position);
  * an identical `[tmdbid-<id>]` already present -> the curly token is dropped
    (no duplicate tag);
  * other providers' square tags (`[tvdbid-…]`, `[imdbid-tt…]`) are PRESERVED
    verbatim;
  * folders already at target shape get NO rename — only a missing NFO.

Safety:
  * DRY-RUN by default — prints the full per-folder plan, mutates nothing;
  * `--apply` loops STRICTLY SEQUENTIALLY (IMP-C24 discipline: never run two
    mutating MediaVault commands in parallel) and re-runs are idempotent —
    folders already migrated no longer match the scan;
  * never overwrites an existing `.nfo` (a local NFO always wins);
  * MANUAL-ONLY: this tool is NEVER invoked by MediaVault (same stance as
    `tools/remux_unsplittable.py`). Template: `tools/migrate_rehash_flag.py`.

Usage:
    python tools/migrate_token_brackets.py               # dry-run report
    python tools/migrate_token_brackets.py --apply       # LIVE: renames folders
    python tools/migrate_token_brackets.py --library series --limit 5
"""
import argparse
import os
import re
import sys

# Repo root on sys.path so `python tools/migrate_token_brackets.py` imports the
# app modules from any CWD (template: tools/migrate_rehash_flag.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mvcommon  # noqa: E402
import main  # noqa: E402

LIBRARY_PREFIXES = {
    "movies": "mov",
    "series": "tv",
    "anime": "ani",
    "others": "oth",
}

# The legacy curly tmdb token, with its id captured (case-insensitive, IMP-C23).
_CURLY_TMDB = re.compile(r"\{tmdb-([^\}]+)\}", re.IGNORECASE)


def _norm(p):
    return os.path.normpath(os.path.abspath(p)).lower() if p else ""


def classify_leaf(leaf):
    """Classify a folder leaf name's token shape.

    Returns (needs_rename, square_tmdb_id_or_None, curly_ids):
      needs_rename       — a curly tmdb token is present
      square_tmdb_id     — id carried by an existing square `[tmdbid-…]` tag
      curly_ids          — ids carried by legacy curly tokens, in order
    """
    square = main.TMDB_TOKEN_SQUARE_RE.search(leaf or "")
    square_id = square.group(0)[len("[tmdbid-"):-1] if square else None
    curly_ids = _CURLY_TMDB.findall(leaf or "")
    return bool(curly_ids), square_id, curly_ids


def target_leaf(leaf, square_id):
    """The migrated leaf name: every curly tmdb token becomes the square form
    (`[tmdbid-<id>]`); a curly token whose id is ALREADY present in square form
    is dropped (no duplicate tag); other providers' square tags are untouched."""
    square_lower = str(square_id).strip().lower() if square_id is not None else None

    def _repl(m):
        cid = m.group(1).strip()
        if square_lower is not None and cid.lower() == square_lower:
            return " "  # duplicate identity — drop the legacy curly token
        return f"[tmdbid-{cid}]"

    out = _CURLY_TMDB.sub(_repl, leaf)
    return re.sub(r"\s{2,}", " ", out).strip()


def _nfo_kind_for(ids):
    """movie -> movie.nfo; anything else (tv/ani/oth show or season folder) -> tvshow.nfo."""
    return "movie" if ids and all(i.startswith("mov") for i in ids) else "tvshow"


def scan(library_filter=None):
    """Scan the (sandboxed-or-live) library for tokened folders.

    Returns a list of plans sorted by folder path:
      {"old_path", "leaf", "needs_rename", "new_leaf", "square_id", "curly_ids",
       "ids", "kind", "tmdb_id", "has_nfo"}
    """
    prefix = LIBRARY_PREFIXES.get(library_filter) if library_filter else None
    library = mvcommon.load_library()

    folders = {}  # norm path -> {"path", "ids"}
    for mid, entry in library.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "multi_ep_alias":
            continue  # non-physical: no folder of its own
        fp = entry.get("folder_path")
        if not fp:
            continue
        if prefix and not mid.startswith(prefix):
            continue
        key = _norm(fp)
        folders.setdefault(key, {"path": fp, "ids": []})
        if mid not in folders[key]["ids"]:
            folders[key]["ids"].append(mid)

    plans = []
    for key in sorted(folders):
        info = folders[key]
        leaf = os.path.basename(os.path.normpath(info["path"]))
        needs_rename, square_id, curly_ids = classify_leaf(leaf)
        ids = info["ids"]
        kind = _nfo_kind_for(ids)
        # id source: the square/curly token wins (it is what the name promises);
        # else the library's metadata.tmdb_id (e.g. a [tvdbid-…]-only folder).
        tmdb_id = None
        if square_id is not None:
            tmdb_id = square_id
        elif curly_ids:
            tmdb_id = curly_ids[0]
        else:
            for mid in ids:
                meta = (library.get(mid) or {}).get("metadata") or {}
                if meta.get("tmdb_id") is not None:
                    tmdb_id = meta["tmdb_id"]
                    break
        new_leaf = target_leaf(leaf, square_id) if needs_rename else leaf
        nfo_path = os.path.join(info["path"], "movie.nfo" if kind == "movie" else "tvshow.nfo")
        plans.append({
            "old_path": info["path"],
            "leaf": leaf,
            "needs_rename": needs_rename,
            "new_leaf": new_leaf,
            "square_id": square_id,
            "curly_ids": curly_ids,
            "ids": ids,
            "kind": kind,
            "tmdb_id": tmdb_id,
            "has_nfo": os.path.exists(nfo_path),
        })
    return plans


def _print_report(plans):
    rename_n = sum(1 for p in plans if p["needs_rename"])
    square_n = sum(1 for p in plans if not p["needs_rename"] and p["square_id"] is not None)
    other_n = len(plans) - rename_n - square_n
    print("=" * 78)
    print(f"  IMP-U6 token migration — {len(plans)} tokened folder(s) found")
    print(f"    legacy curly (needs rename) : {rename_n}")
    print(f"    already square [tmdbid-…]   : {square_n}")
    print(f"    other-provider tags only    : {other_n}")
    print("=" * 78)
    for p in plans:
        tag = "RENAME" if p["needs_rename"] else "  ok  "
        line = f"  [{tag}] {p['leaf']}"
        if p["needs_rename"]:
            line += f"\n          -> {p['new_leaf']}"
        line += f"\n          ids: {', '.join(p['ids'])}  kind: {p['kind']}  tmdb_id: {p['tmdb_id']}"
        line += f"  nfo: {'present' if p['has_nfo'] else 'WILL WRITE' if p['tmdb_id'] is not None else 'no id — skipped'}"
        print(line)
    print("=" * 78)


def _write_nfo_for(plan, folder):
    """Best-effort id-first NFO into `folder` (offline — no api_key, so the base
    title/year/uniqueid elements only). Never overwrites (D6, inside _write_nfo)."""
    if plan["tmdb_id"] is None:
        return False
    library = mvcommon.load_library()
    meta = {}
    for mid in plan["ids"]:
        meta = (library.get(mid) or {}).get("metadata") or {}
        if meta.get("title"):
            break
    title = meta.get("title") or main.strip_tmdb_tokens(plan["leaf"]) or plan["leaf"]
    main._write_nfo(
        folder,
        kind=plan["kind"],
        title=title,
        year=meta.get("year"),
        tmdb_id=plan["tmdb_id"],
        overview=meta.get("overview", ""),
        vote_average=None,
        api_key=None,  # OFFLINE: the minimal id-first NFO (no TMDB fetches)
    )
    return True


def migrate(apply=False, library_filter=None, limit=None, verbose=True):
    """Run the migration. Dry-run by default; `apply=True` performs the renames
    + NFO writes sequentially. Returns the summary counts dict."""
    plans = scan(library_filter)
    if limit is not None:
        plans = plans[:limit]
    if verbose:
        _print_report(plans)

    summary = {"found": len(plans),
               "needs_rename": sum(1 for p in plans if p["needs_rename"]),
               "renamed": 0, "rename_failed": 0,
               "nfos_written": 0, "nfos_kept": 0, "nfos_skipped_no_id": 0}
    if not apply:
        if verbose:
            print("DRY-RUN — nothing was touched. Re-run with --apply to perform it.")
        return summary

    print("\n🚀 APPLY: renaming folders strictly sequentially (journal-backed)…")
    for plan in plans:
        if plan["needs_rename"]:
            print(f"   > renaming: {plan['leaf']}  ->  {plan['new_leaf']}")
            ok = main.cmd_rename_folder(plan["old_path"], plan["new_leaf"])
            if not ok:
                summary["rename_failed"] += 1
                print(f"   ⚠️  rename FAILED for {plan['old_path']} — continuing with the rest.")
                continue
            summary["renamed"] += 1
            folder = os.path.join(os.path.dirname(os.path.normpath(plan["old_path"])),
                                  plan["new_leaf"])
        else:
            folder = plan["old_path"]
        # NFO leg (both renamed and already-square folders; never overwrites).
        nfo_name = "movie.nfo" if plan["kind"] == "movie" else "tvshow.nfo"
        if os.path.exists(os.path.join(folder, nfo_name)):
            summary["nfos_kept"] += 1
        elif plan["tmdb_id"] is not None and _write_nfo_for(plan, folder):
            summary["nfos_written"] += 1
        else:
            summary["nfos_skipped_no_id"] += 1

    if verbose:
        print(f"\n=== MIGRATED: renamed={summary['renamed']} failed={summary['rename_failed']} "
              f"nfos_written={summary['nfos_written']} nfos_kept={summary['nfos_kept']} "
              f"nfos_skipped_no_id={summary['nfos_skipped_no_id']} ===")
        print("Re-run this tool (dry-run) to confirm 0 pending folders remain.")
    return summary


def main_cli():
    ap = argparse.ArgumentParser(
        description="IMP-U6 one-shot: {tmdb-…} curly folder tokens -> [tmdbid-…] square + NFO backfill. "
                    "DRY-RUN by default; --apply performs the LIVE renames.")
    ap.add_argument("--apply", action="store_true",
                    help="LIVE: perform the renames + NFO writes (default: dry-run report only)")
    ap.add_argument("--library", choices=sorted(LIBRARY_PREFIXES), default=None,
                    help="restrict the migration to one library category")
    ap.add_argument("--limit", type=int, default=None,
                    help="process at most N folders (a controlled first run)")
    args = ap.parse_args()
    if args.apply:
        print("⚠️  LIVE OPERATION: folders under C:\\Media will be RENAMED (journal-backed, "
              "hash-safe, sequential). Library JSONs are NOT backed up by this tool — "
              "back up C:\\Media\\library_*.json first if you have not already.")
    return migrate(apply=args.apply, library_filter=args.library, limit=args.limit)


if __name__ == "__main__":
    main_cli()
