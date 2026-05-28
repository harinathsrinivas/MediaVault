# Execution Log

Task: IMP-C11 — Hash-mismatch quarantine in cmd_restore

## Step 1 — [status: done]
- Executor: orchestrator (direct; subagent Task tool unavailable in this run)
- Model: opus
- Files changed: `main.py` (added one top-level helper)
- Outcome: Added `quarantine_restore_file(restore_folder, filename)` at main.py:1062, immediately before `cmd_restore`. It creates `<restore_folder>/quarantine/` (exist_ok=True), builds a colon-free timestamped destination `<filename>.<YYYYmmddTHHMMSS>`, applies a same-second collision counter (`.1`, `.2`, ...), `shutil.move`s the bad file there, and returns the final dest path. No call site yet — purely additive.
- Key decisions: main.py imports datetime as `from datetime import datetime` (line 13), so the helper uses `datetime.now()`. `shutil` is already imported (line 6). No new imports added. Steps 2/3 must match this `datetime.now()` form.
- Verification: `.venv\Scripts\python.exe -c "import main"` imports cleanly (attr present). Functional smoke test: moved a temp restore/ file into restore/quarantine/<name>.<ts> and returned that path (SMOKE PASS). Collision test: quarantining the same filename twice produced two distinct files, neither overwritten (COLLISION PASS).

## Step 2 — [status: done]
- Executor: orchestrator (direct)
- Model: opus
- Files changed: `main.py` (standard-path failure branch of `cmd_restore` only)
- Outcome: In the single-file (non-split) path of `cmd_restore`, the hash-mismatch failure branch (main.py:~1124) now calls `q = quarantine_restore_file(restore_folder, filename)`, prints `❌ Hash mismatch. Bad file quarantined at {q}. A fresh fetch will re-download.`, and returns False — instead of leaving the bad file in restore/. Wrapped in try/except: if the move is blocked, it falls back to the original `❌ Error: Restore file hash mismatch! Corrupt?` message (leave-in-place) and still returns False, so behavior is never worse than before. The success branch (`shutil.move(source_path, target_path)`, empty-restore/ cleanup, `status="restored_local"`) is untouched.
- Key decisions: Passed the in-scope locals `restore_folder` (=`os.path.join(local_folder, RESTORE_DIR_NAME)`) and `filename` (=`entry['filename']`) directly to the helper — `source_path` is `os.path.join(restore_folder, filename)`, so the helper receives exactly the bad file. Greppable substring tests assert on is `Hash mismatch. Bad file quarantined at` (emoji prefix kept separate so it can't break the grep).
- Verification: import clean; `grep -c "Bad file quarantined at"` = 1; success-path lines `shutil.move(source_path, target_path)` and `restored_local` still present. Behavioral (sandbox, LIBRARY_* monkeypatched): mismatch -> RETURN_FALSE, ORIG_GONE from restore/, exactly 1 file under restore/quarantine/ named `film.mkv.<ts>`, diagnostic in stdout, target dummy untouched. Success (matching hash) -> RETURN_TRUE, good bytes moved into folder_path, NO quarantine dir, status restored_local, restore/ cleaned.

## Step 3 — [status: done]
- Executor: orchestrator (direct)
- Model: opus
- Files changed: `main.py` (split path of `cmd_restore`, inserted block only)
- Outcome: Added a pre-merge per-chunk SHA256 verification loop (main.py:1100-1125) AFTER the existence check and BEFORE the `merge_video_files` call. It iterates `chunks_meta`, hashing `os.path.join(restore_folder, c['filename'])` and comparing to `c['hash']`, collecting offending filenames into `bad_chunks`. If any are bad: it quarantines ONLY the offending chunk(s) via `quarantine_restore_file` (clean chunks stay in restore/ for a targeted re-fetch), prints the greppable diagnostic per bad chunk, deletes any stale partial merged output at `target_path` via `os.remove` (guarded by exists + try/except), and returns False — so `merge_video_files` never runs on corrupt input. If all chunks pass, control falls through to the unchanged merge -> re-hash -> cleanup -> `status="restored_local"` -> return True success path.
- Key decisions: Reused the existing in-scope locals: `chunks_meta` (= `entry["split_info"]["chunks"]`), `restore_folder`, and `target_path` (= `os.path.join(local_folder, filename)`, the merge destination). Quarantine call per offending chunk wrapped in try/except mirroring the standard path's fallback. Stale partial-output deletion uses `os.remove` (NOT quarantine) since it is reproducible from chunks. Diagnostic wording identical to the standard path.
- Verification: import clean; `grep -c "Bad file quarantined at"` = 2 (standard + split); the merge call `if merge_video_files(chunk_paths_in_restore, target_path):` and `restored_local` both still present (success path untouched). Behavioral (sandbox, LIBRARY_* monkeypatched, 2 chunks where chunk.002 is corrupt, plus a stale partial merge at target): RETURN_FALSE; BAD_CHUNK_GONE from restore/; CLEAN_CHUNK_STAYS in restore/; exactly 1 file quarantined named `film.chunk.002.mkv.<ts>`; PARTIAL_MERGE_DELETED (target removed); diagnostic in stdout. merge_video_files never reached (so no mkvmerge dependency in this path).
