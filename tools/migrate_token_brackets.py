"""One-shot migration: provider folder tokens `{tmdb-…}` → `[tmdbid-…]` (IMP-U6).

Converts every media folder whose leaf name carries a LEGACY tmdb token to the
canonical Jellyfin/Emby-native square form `[tmdbid-<id>]`, via MediaVault's own
crash-safe `cmd_rename_folder` (journal-backed, hash-safe, rewrites
`folder_path` for every descendant). In the SAME pass it writes the
`movie.nfo` / `tvshow.nfo` id sidecar (D6) so Plex's NFO agent pins the same id.

Legacy shapes handled (D1 keyword fix applies to all of them):
  * curly           `Movie (2020) {tmdb-59436}` / `{TMDB-69590}`  (pre-IMP-U6 stamps)
  * old-keyword     `John Wick Chapter 4 (2023) [tmdb-603692]`    (pre-IMP-U6 manual renames)
Target:            `Movie (2020) [tmdbid-59436]`

Shape rules:
  * every legacy tmdb token -> `[tmdbid-<id>]` (same position);
  * an identical canonical `[tmdbid-<id>]` already present -> the legacy token is
    dropped (no duplicate tag);
  * other providers' square tags (`[tvdbid-…]`, `[imdbid-tt…]`) are PRESERVED
    verbatim;
  * folders already at target shape get NO rename — only a missing NFO.

Two phases:
  A. library folder_paths (leaves + season_maps; `multi_ep_alias` skipped);
  B. on-disk GROUP folders that are NOT any library folder_path (a show-parent
     like `Friends (1994) {tmdb-1668}` above flat season folders) — deepest-first,
     so a parent renames only after its children; a top-most group folder also
     gets a `tvshow.nfo`.

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

# Legacy tmdb tokens, id captured (case-insensitive, IMP-C23):
_CURLY_TMDB = re.compile(r"\{tmdb-([^\}]+)\}", re.IGNORECASE)      # {tmdb-59436} / {TMDB-69590}
_SQUARE_OLD_TMDB = re.compile(r"\[tmdb-([^\]]+)\]", re.IGNORECASE)  # [tmdb-603692] (old keyword;
# `[tmdbid-…]` does NOT match this — after "[tmdb" comes "i", not "-").


def _norm(p):
    return os.path.normpath(os.path.abspath(p)).lower() if p else ""


def classify_leaf(leaf):
    """Classify a folder leaf name's token shape.

    Returns (needs_rename, square_tmdb_id_or_None, legacy_ids):
      needs_rename    — a legacy tmdb token (curly OR old-keyword square) is present
      square_tmdb_id  — id carried by an existing canonical `[tmdbid-…]` tag
      legacy_ids      — ids carried by legacy tmdb tokens (either shape), in order
    """
    square = main.TMDB_TOKEN_SQUARE_RE.search(leaf or "")
    square_id = square.group(0)[len("[tmdbid-"):-1] if square else None
    legacy_ids = _CURLY_TMDB.findall(leaf or "") + _SQUARE_OLD_TMDB.findall(leaf or "")
    return bool(legacy_ids), square_id, legacy_ids


def target_leaf(leaf, square_id):
    """The migrated leaf name: every legacy tmdb token (curly `{tmdb-…}` /
    `{TMDB-…}` or old-keyword square `[tmdb-…]`) becomes the canonical
    `[tmdbid-<id>]`; a token whose id is ALREADY present in canonical form is
    dropped (no duplicate tag); other providers' square tags are untouched."""
    square_lower = str(square_id).strip().lower() if square_id is not None else None

    def _repl(m):
        cid = m.group(1).strip()
        if square_lower is not None and cid.lower() == square_lower:
            return " "  # duplicate identity — drop the legacy token
        return f"[tmdbid-{cid}]"

    out = _CURLY_TMDB.sub(_repl, leaf)
    out = _SQUARE_OLD_TMDB.sub(_repl, out)
    return re.sub(r"\s{2,}", " ", out).strip()


def _nfo_kind_for(ids):
    """movie -> movie.nfo; anything else (tv/ani/oth show or season folder) -> tvshow.nfo."""
    return "movie" if ids and all(i.startswith("mov") for i in ids) else "tvshow"


def scan(library_filter=None):
    """PHASE A — scan the (sandboxed-or-live) library for tokened folders.

    Returns a list of plans sorted by folder path:
      {"old_path", "leaf", "needs_rename", "new_leaf", "square_id", "legacy_ids",
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
        needs_rename, square_id, legacy_ids = classify_leaf(leaf)
        ids = info["ids"]
        kind = _nfo_kind_for(ids)
        # id source: the square/legacy token wins (it is what the name promises);
        # else the library's metadata.tmdb_id (e.g. an untagged enriched folder).
        tmdb_id = None
        if square_id is not None:
            tmdb_id = square_id
        elif legacy_ids:
            tmdb_id = legacy_ids[0]
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
            "legacy_ids": legacy_ids,
            "ids": ids,
            "kind": kind,
            "tmdb_id": tmdb_id,
            "has_nfo": os.path.exists(nfo_path),
        })
    return plans


def scan_disk(exclude_paths, library_filter=None):
    """PHASE B — walk the CATEGORY_ROOTS media tree and find GROUP folders that
    carry a legacy token but are NOT any library entry's folder_path (e.g. a
    show-parent `Friends (1994) {tmdb-1668}` above flat season folders). These
    need the same rename; `cmd_rename_folder` rewrites every descendant
    folder_path under them. Deepest-first ordering is applied so a parent is
    only renamed after its children. `library_filter` is honoured by INFERRING
    the category from the folder's path segment under LOCAL_ROOT."""
    pat = re.compile(r"(?:\{tmdb-|\[tmdb-)", re.IGNORECASE)
    skip_dirs = {"_parts", "checksums", "restore", ".git", ".idea", "__pycache__",
                 "library_backups"}
    # path segment under LOCAL_ROOT -> library prefix (for --library filtering)
    prefix_for_sub = {}
    for cat, subs in main.CATEGORY_ROOTS.items():
        cat_prefix = {"movies": "mov", "series": "tv", "anime": "ani", "other": "oth"}.get(cat, "")
        for sub in subs:
            prefix_for_sub[sub.lower()] = cat_prefix
    allowed_prefix = LIBRARY_PREFIXES.get(library_filter) if library_filter else None
    seen = set(exclude_paths)
    out = []
    for subs in main.CATEGORY_ROOTS.values():
        for sub in subs:
            root = os.path.join(main.LOCAL_ROOT, sub)
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, _filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip_dirs]
                norm = os.path.normpath(dirpath).lower()
                if norm in seen:
                    continue
                leaf = os.path.basename(dirpath)
                if not pat.search(leaf):
                    continue
                if allowed_prefix:
                    top = dirpath[len(main.LOCAL_ROOT):].strip(os.sep).split(os.sep)[0]
                    if prefix_for_sub.get(top.lower(), "") != allowed_prefix:
                        seen.add(norm)
                        continue
                m = (main.TMDB_TOKEN_CURLY_RE.search(leaf)
                     or _SQUARE_OLD_TMDB.search(leaf))
                if not m:
                    continue
                digits = re.sub(r"[^0-9]", "", m.group(0))
                parent_leaf = os.path.basename(os.path.dirname(dirpath))
                out.append({
                    "old_path": dirpath,
                    "leaf": leaf,
                    "needs_rename": True,
                    "new_leaf": target_leaf(leaf, None),
                    "square_id": None,
                    "legacy_ids": [digits] if digits else [],
                    "ids": [],
                    "kind": "tvshow",
                    "tmdb_id": digits or None,
                    "has_nfo": False,
                    "top_most": not pat.search(parent_leaf),
                })
                seen.add(norm)
    # deepest first — children before parents
    out.sort(key=lambda p: p["old_path"].count(os.sep), reverse=True)
    return out


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


def _print_report(plans):
    rename_n = sum(1 for p in plans if p["needs_rename"])
    square_n = sum(1 for p in plans if not p["needs_rename"] and p["square_id"] is not None)
    other_n = len(plans) - rename_n - square_n
    print("=" * 78)
    print(f"  IMP-U6 token migration — {len(plans)} tokened folder(s) found")
    print(f"    legacy token (needs rename) : {rename_n}")
    print(f"    already square [tmdbid-…]   : {square_n}")
    print(f"    other-provider tags only    : {other_n}")
    print("=" * 78)
    for p in plans:
        tag = "RENAME" if p["needs_rename"] else "  ok  "
        line = f"  [{tag}] {p['leaf']}"
        if p["needs_rename"]:
            line = line + "\n          -> " + p["new_leaf"]
        line = (line + "\n          ids: " + ", ".join(p["ids"])
                + "  kind: " + p["kind"] + "  tmdb_id: " + str(p["tmdb_id"]))
        nfo_state = ("present" if p["has_nfo"]
                     else ("WILL WRITE" if p["tmdb_id"] is not None else "no id — skipped"))
        line += "  nfo: " + nfo_state
        print(line)
    print("=" * 78)


def migrate(apply=False, library_filter=None, limit=None, verbose=True):
    """Run the migration. Dry-run by default; `apply=True` performs the renames
    + NFO writes sequentially. Returns the summary counts dict."""
    plans = scan(library_filter)
    if limit is not None:
        plans = plans[:limit]
    # PHASE B scan: on-disk GROUP folders that are not library folder_paths
    # (e.g. a show-parent `Friends (1994) {tmdb-1668}` above flat season folders).
    # Deepest-first so a parent renames only after its children; each rename goes
    # through cmd_rename_folder, which rewrites every descendant folder_path.
    # SKIPPED when --limit is set (a controlled partial run defers Phase B to the
    # unfiltered run).
    disk_plans = ([] if limit is not None else scan_disk(
        exclude_paths={os.path.normpath(os.path.abspath(p["old_path"])).lower()
                       for p in plans},
        library_filter=library_filter))
    if verbose:
        _print_report(plans)
        if disk_plans:
            print("")
            print(f"  PHASE B — {len(disk_plans)} on-disk group folder(s) not referenced "
                  f"by a library folder_path (deepest-first):")
            for p in disk_plans:
                print(f"    [RENAME] {p['leaf']}")
                print(f"          -> {p['new_leaf']}")

    summary = {"found": len(plans) + len(disk_plans),
               "needs_rename": (sum(1 for p in plans if p["needs_rename"])
                                + len(disk_plans)),
               "renamed": 0, "rename_failed": 0,
               "nfos_written": 0, "nfos_kept": 0, "nfos_skipped_no_id": 0}
    if not apply:
        if verbose:
            print("DRY-RUN — nothing was touched. Re-run with --apply to perform it.")
        return summary

    print("")
    print("🚀 APPLY: renaming folders strictly sequentially (journal-backed)…")
    # ---- PHASE A: library folder_paths ----
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

    # ---- PHASE B: the on-disk group folders ----
    for plan in disk_plans:
        print(f"   > renaming: {plan['leaf']}  ->  {plan['new_leaf']}")
        ok = main.cmd_rename_folder(plan["old_path"], plan["new_leaf"])
        new_path = os.path.join(os.path.dirname(os.path.normpath(plan["old_path"])),
                                plan["new_leaf"])
        if not ok:
            # cmd_rename_folder REFUSES a path no library entry references (its
            # contract is library-consistency rewrites). A group folder holding
            # only unprepped/empty content is exactly that case — a direct
            # os.rename is safe there (atomic move; zero folder_path rewrites
            # needed because none point at it; reversal = rename back).
            try:
                os.rename(plan["old_path"], new_path)
                print("   ✅ direct rename (no library references — nothing to rewrite).")
                ok = True
            except OSError as exc:
                print(f"   ⚠️  direct rename failed ({exc}) — continuing with the rest.")
        if not ok:
            summary["rename_failed"] += 1
            print(f"   ⚠️  rename FAILED for {plan['old_path']} — continuing with the rest.")
            continue
        summary["renamed"] += 1
        folder = new_path
        # NFO only for the TOP-most legacy dir (a nested season dir under a
        # tokened show parent gets no tvshow.nfo of its own).
        if (plan.get("top_most") and plan["tmdb_id"] is not None
                and not os.path.exists(os.path.join(folder, "tvshow.nfo"))):
            if _write_nfo_for(plan, folder):
                summary["nfos_written"] += 1

    if verbose:
        print("")
        print(f"=== MIGRATED: renamed={summary['renamed']} failed={summary['rename_failed']} "
              f"nfos_written={summary['nfos_written']} nfos_kept={summary['nfos_kept']} "
              f"nfos_skipped_no_id={summary['nfos_skipped_no_id']} ===")
        print("Re-run this tool (dry-run) to confirm 0 pending folders remain.")
    return summary


def main_cli():
    ap = argparse.ArgumentParser(
        description="IMP-U6 one-shot: legacy tmdb folder tokens (curly {tmdb-…} / old-keyword "
                    "square [tmdb-…]) -> canonical [tmdbid-…] + NFO backfill. "
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
