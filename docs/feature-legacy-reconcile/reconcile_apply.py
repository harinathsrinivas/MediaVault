"""Legacy text-dummy reconciliation for the 107 user-verified entries.

Reads C:\\Media\\legacy_reconcile_decisions.json (all verdict=yes, bucketed via
the `note` field). For each entry:
  * NON-split  -> mark status=archived, uploaded=true.
  * SPLIT      -> reconstruct split_info from the local checksums/ sidecars
                  (filename+hash per chunk, ordered by .chunk.NNN.), then mark
                  archived+uploaded, and stamp a `split_info_reconstructed` marker.
Then (optionally) regenerate the 126/81-byte TEXT dummies into proper VIDEO
dummies via main.make_video_dummy (same recipe cmd_repair_dummies uses).

Default = DRY RUN (no writes). Pass --apply to mutate. Always backs up the 3
library JSONs first when applying. Verifies, never guesses; flags anomalies.
"""
import json, os, re, sys, shutil, datetime

DRY = "--apply" not in sys.argv
DO_DUMMY = "--no-dummy" not in sys.argv  # dummy conversion on by default in --apply

LIB = {
    'movies': r'C:\Media\library_movies.json',
    'series': r'C:\Media\library_series.json',
    'anime':  r'C:\Media\library_anime.json',
}
DECISIONS = r'C:\Media\legacy_reconcile_decisions.json'
DUMMY_MAX = 200000
TS = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')

CHUNK_RE = re.compile(r'\.chunk\.(\d+)\.', re.IGNORECASE)


def bucket_of(note):
    n = (note or '').lower()
    if 'non split' in n or 'non-split' in n:
        return 'nonsplit'
    if 'into 2 chunks' in n:
        return 'split2'
    if 'into 3 chunks' in n:
        return 'split3'
    return 'unknown'


def reconstruct_split_info(folder_path, short_id):
    """Build (split_info, problems) from <folder>/checksums/*[short_id]*.chunk.NNN.mkv.sha256."""
    problems = []
    cdir = os.path.join(folder_path, 'checksums')
    if not os.path.isdir(cdir):
        return None, [f"no checksums/ dir at {cdir}"]
    tag = f'[{short_id}]'
    found = []
    for fn in os.listdir(cdir):
        if not fn.lower().endswith('.sha256'):
            continue
        if tag not in fn or '.chunk.' not in fn.lower():
            continue
        m = CHUNK_RE.search(fn)
        if not m:
            problems.append(f"can't parse chunk# from {fn}")
            continue
        num = int(m.group(1))
        chunk_name = fn[:-len('.sha256')]  # strip trailing .sha256
        try:
            with open(os.path.join(cdir, fn), encoding='utf-8', errors='replace') as f:
                content = f.read().strip()
        except OSError as e:
            problems.append(f"read err {fn}: {e}")
            continue
        # format: "<hash> *<chunk_name>"
        parts = content.split(None, 1)
        chash = parts[0] if parts else ''
        if not re.fullmatch(r'[0-9a-fA-F]{64}', chash):
            problems.append(f"bad hash in {fn}: {chash!r}")
            continue
        # sanity: the referenced name should match chunk_name
        if len(parts) > 1:
            ref = parts[1].lstrip('*').strip()
            if ref != chunk_name:
                problems.append(f"name mismatch in {fn}: sidecar says {ref!r}")
        found.append((num, chunk_name, chash))
    if not found:
        return None, problems + [f"no chunk sidecars matching {tag} in checksums/"]
    found.sort(key=lambda t: t[0])
    nums = [n for n, _, _ in found]
    if nums != list(range(1, len(nums) + 1)):
        problems.append(f"non-contiguous chunk numbers: {nums}")
    chunks = [{"filename": name, "hash": h} for _, name, h in found]
    split_info = {
        "is_split": True,
        "method": "reconstructed_from_checksums",  # not read by fetch/restore
        "val": None,
        "total_chunks": len(chunks),
        "chunks": chunks,
    }
    return split_info, problems


def main():
    dec = json.load(open(DECISIONS, encoding='utf-8'))['decisions']
    by_id = {d['id']: d for d in dec}
    libs = {cat: json.load(open(p, encoding='utf-8')) for cat, p in LIB.items()}
    # map id -> (cat) by which lib contains it
    plan = []  # (cat, id, action_dict)
    anomalies = []   # BLOCKING — abort apply
    warnings = []    # non-blocking — recorded, apply proceeds
    counts = {'nonsplit': 0, 'split2': 0, 'split3': 0, 'unknown': 0}
    for mid, d in by_id.items():
        if d.get('verdict') != 'yes':
            anomalies.append(f"{mid}: verdict={d.get('verdict')} (not 'yes') — SKIPPED")
            continue
        bucket = bucket_of(d.get('note'))
        counts[bucket] += 1
        # locate the entry
        cat = None
        for c, lib in libs.items():
            if mid in lib:
                cat = c; break
        if cat is None:
            anomalies.append(f"{mid}: not found in any library file")
            continue
        e = libs[cat][mid]
        fp = e.get('folder_path'); fn = e.get('filename')
        full = os.path.join(fp, fn) if fp and fn else None
        # validate it's still a tiny dummy + has a real original hash + a search_term
        try:
            sz = os.path.getsize(full)
        except OSError:
            anomalies.append(f"{mid}: on-disk file missing ({full})")
            continue
        if sz >= DUMMY_MAX:
            anomalies.append(f"{mid}: on-disk is already a REAL file ({sz}B) — skipping, not a dummy")
            continue
        if not re.fullmatch(r'[0-9a-fA-F]{64}', str(e.get('hash') or '')):
            anomalies.append(f"{mid}: entry has no valid original hash — restore-merge verify would lack a target")
        if not e.get('search_term'):
            anomalies.append(f"{mid}: no search_term (GP key) — fetch could not locate it")
        action = {'set_status': 'archived', 'set_uploaded': True, 'bucket': bucket,
                  'short_id': e.get('short_id'), 'folder': fp, 'filename': fn,
                  'cur_status': e.get('status'), 'cur_uploaded': e.get('uploaded'),
                  'had_split': 'split_info' in e}
        if bucket in ('split2', 'split3'):
            si, probs = reconstruct_split_info(fp, e.get('short_id'))
            if si is None:
                anomalies.append(f"{mid}: SPLIT but reconstruction FAILED: {probs}")
                continue
            expected = 2 if bucket == 'split2' else 3
            if si['total_chunks'] != expected:
                # NON-BLOCKING: the checksum sidecars are authoritative (written at
                # push time); the user's bucket label was an eyeball count. Fetch
                # re-verifies every chunk hash, so the count label can't corrupt.
                warnings.append(f"{mid}: bucket label said {expected} chunks but checksum sidecars show "
                                f"{si['total_chunks']} (using the checksum truth)")
            if probs:
                # genuine reconstruction issues (non-contiguous / bad hash / name
                # mismatch) ARE blocking.
                anomalies.append(f"{mid}: reconstruction problems: {probs}")
            action['split_info'] = si
        plan.append((cat, mid, action))

    # ---- report ----
    print(f"=== Reconcile plan ({'DRY RUN' if DRY else 'APPLY'}) — {TS} ===")
    print(f"decisions: {len(by_id)} | planned: {len(plan)} | buckets: {counts}")
    nsplit = sum(1 for _, _, a in plan if 'split_info' in a)
    print(f"split_info reconstructed for: {nsplit} entries")
    # show a few reconstructed examples
    shown = 0
    for cat, mid, a in plan:
        if 'split_info' in a and shown < 4:
            si = a['split_info']
            print(f"  [{mid}] {si['total_chunks']} chunks:")
            for c in si['chunks']:
                print(f"      {c['hash'][:12]}…  {c['filename'][-40:]}")
            shown += 1
    print(f"\nWARNINGS ({len(warnings)}) -- non-blocking:")
    for x in warnings:
        print("  [WARN] ", x)
    if not warnings:
        print("  (none)")
    print(f"\nBLOCKING ANOMALIES ({len(anomalies)}):")
    for x in anomalies:
        print("  [BLOCK] ", x)
    if not anomalies:
        print("  (none -- clean)")

    if DRY:
        print("\nDRY RUN -- no files changed. Re-run with --apply to mutate (will back up first).")
        return 0 if not anomalies else 2

    if anomalies:
        print("\n[ABORT] APPLY refused -- blocking anomalies present. Resolve before mutating.")
        return 2

    # ---- APPLY ----
    bdir = os.path.join(r'C:\Media', f'_mvbackup_reconcile_{TS}')
    os.makedirs(bdir, exist_ok=True)
    for cat, p in LIB.items():
        shutil.copy2(p, os.path.join(bdir, os.path.basename(p)))
    print(f"\nBACKUP -> {bdir}")
    # mutate in memory
    for cat, mid, a in plan:
        e = libs[cat][mid]
        e['status'] = 'archived'
        e['uploaded'] = True
        if 'split_info' in a:
            e['split_info'] = a['split_info']
            e['split_info_reconstructed'] = True
            e['reconstructed_at'] = TS
    # write back each lib file (preserve indent=4, utf-8, ensure_ascii=False)
    changed_files = set(cat for cat, _, _ in plan)
    for cat in changed_files:
        # Match save_library's format exactly: indent=4, ensure_ascii default (True),
        # text-mode write (CRLF on Windows) — so this stays a minimal, consistent diff.
        with open(LIB[cat], 'w', encoding='utf-8') as f:
            json.dump(libs[cat], f, indent=4)
        print(f"  wrote {LIB[cat]}")
    # provenance report
    rep = os.path.join(r'C:\Media', f'reconcile_applied_{TS}.json')
    with open(rep, 'w', encoding='utf-8') as f:
        json.dump({'ts': TS, 'backup': bdir, 'warnings': warnings,
                   'applied': [{'id': mid, 'cat': cat, 'bucket': a['bucket'],
                                'reconstructed_chunks': a.get('split_info', {}).get('total_chunks'),
                                'prev_status': a['cur_status'], 'prev_uploaded': a['cur_uploaded']}
                               for cat, mid, a in plan]}, f, indent=2)
    print(f"  provenance -> {rep}")

    # ---- dummy conversion ----
    if DO_DUMMY:
        sys.path.insert(0, r'C:\Users\harin\PycharmProjects\MediaVault')
        import main as mv
        conv = 0; failed = 0; skipped = 0
        for cat, mid, a in plan:
            full = os.path.join(a['folder'], a['filename'])
            try:
                if os.path.getsize(full) >= DUMMY_MAX:
                    skipped += 1; continue
            except OSError:
                failed += 1; continue
            ext = os.path.splitext(a['filename'])[1]
            tmp = full + '.repair_tmp' + ext
            if mv.make_video_dummy(tmp, ext):
                os.replace(tmp, full)
                conv += 1
            else:
                failed += 1
        print(f"\nDUMMY CONVERSION: converted {conv}, skipped {skipped}, failed {failed}")
    print("\n✅ APPLY complete.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
