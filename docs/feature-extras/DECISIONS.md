# IMP-D19 — Extras option: Locked Decisions

**Task:** add an `--extras` option (Specials / Trailers / Behind-the-Scenes) end-to-end — IMP-D19.
**Branch:** `feature/imp_d19_extras`.
**Decided:** 2026-06-29 (Decision Cards A–E answered by the user; Card C revised same day).
**Status:** LOCKED. These choices supersede any historical "recommended" text in `PLAN.md`. A resuming session must
treat this file as authoritative and NOT re-open these decisions.

---

## Card A — Extras JSON shape + restore layout + de-dup — **CHOSEN: A2 (grouped per source folder)**
The `extras` block nests on the **title** entry (season_map for series/anime/others; the movie leaf for movies),
**grouped per source folder**:

```jsonc
entry["extras"] = {
  "groups": {
    "<group_rel>": {                         // e.g. "Specials", "Extra", or nested "Bonus/Trailers"
      "added_date": "2026-06-29",
      "items": [
        {
          "filename": "S2.Trailer.mkv",
          "sub_rel":  "S2.Trailer.mkv",      // path within the group (just the filename when flat)
          "short_id": "...",                 // generate_short_id(title_id + "::" + group_rel + "/" + sub_rel)
          "hash":     "sha256...",
          "status":   "local_ready",         // -> onboarded -> archived -> restored_local
          "uploaded": false,
          "search_term": "...",
          "tech_spec": { ... },
          "split_info": { ... },             // optional, when chunked
          "re_hashed":  true                 // optional
        }
      ]
    }
  }
}
```

- **Group key `group_rel`** = the extra folder's path **relative to the title's `folder_path`** (`os.path.relpath`),
  falling back to the folder basename if it is not under the title folder.
- **`sub_rel`** = the file's path **within that group** (just the filename for flat folders — both real samples are flat
  one level: Stranger Things `Specials\`, Death Note `Extra\`).
- **`"groups"` wrapper** keeps an arbitrary folder name from ever colliding with a reserved key.
- **Restore** recreates `<title folder_path>/<group_rel>/<sub_rel>` (restore-in-place is guaranteed).
  *Rationale refinement (2026-07-27, Step 12 research):* server recognition depends on the folder NAME being in the
  server's recognized extras list — `Specials`/`Extra` are NOT recognized by Jellyfin/Plex (Emby accepts `specials`);
  see `improvements/JELLYFIN_SETUP_GUIDE.md` §3.6. The A2 decision itself is unaffected.
- **De-dup / additive identity = per-group by normalized `sub_rel`.** Re-adding a group is a no-op for unchanged files;
  adding a new folder appends a new group; adding Specials then Trailers == adding both at once. A changed-byte file
  (hash differs) updates that item and resets `uploaded=false`.
- Paths are **relative**, so `rename_folder` (IMP-D17) needs no extras change.

*(Planner had recommended A1 — a flat `items` list keyed by `rel_path`. User chose A2 for readability.)*

## Card B — CLI surface — **CHOSEN: B1**
- `--extras` / `-extras "<f1>;<f2>"` — semicolon-separated **and** repeatable (`--extras A --extras B`); both additive.
- `--extras-size` / `-extras-size <value>` — **independent** extras chunking: `none` (whole-file), a size with unit
  (`9900mb`, `8gb` → `SIZE_MB` via `parse_size_str`), or `SIZE_MB|SIZE_GB|COUNT <val>`.
- Accepted on: `prep`, `prep_season`, `push`, `push_group`, `prep_push_rep`, `prep_push_rep_season`.
- **New dedicated command** `add_extras <title_id> "<folders>" [--extras-size <v|none>] [device <id_or_name>]
  [no-replace]` for attaching extras to an existing/archived/local-only title.
- **`--extras-size` default = inherit the main split** (same method/value as the command's main `SIZE_*`); if the
  command has no main split, push extras whole/unchunked. Override per-run as needed (e.g. main `SIZE_MB 5000`, extras
  `9900mb` or `none`).

## Card C — Extras fetch — **REVISED 2026-06-29: FLAG-ONLY (no prompt)**
- `fetch` and `fetch_restore` take a **`--fetchExtras` boolean flag** (aliases `--fetch-extras`, `--extras`, `--extra`).
- When set, every extra group is fetched and restored into its respective `Specials\` / `Extra\` subfolder.
- **No interactive prompt at all.** Flag absent → no extras (today's behavior, byte-for-byte). The flag is parsed in
  `main.py` and forwarded verbatim to `mainfetch` (which stays flag-driven / non-interactive).
- `episodes N-M` filters only episodes; extras are all-or-nothing (the flag fetches every group).
- The web-worker / non-TTY path simply never passes the flag and never blocks.

*(Originally answered C1 = a default-No interactive prompt + `--fetchExtras`. The user then revised it to flag-only,
removing the prompt — simpler and eliminates the web-worker prompt-blocking concern.)*

## Card D — Existing / archived / local-only titles + reclaim — **CHOSEN: D1 (full lifecycle)**
- Extras get the **full push → replace(dummy) → fetch → restore lifecycle**, same as main content (Death Note `Extra\`
  alone is ~25 GB, so reclaim matters; "fetch extras" only makes sense if extras were dummied).
- `add_extras` = scan+merge → push extras → replace extras to dummies in one shot, **without touching main content**
  (safe for an archived title whose main is already a dummy). `no-replace` keeps extras local (upload-only).
- `push <id> --extras` on an **archived** main detects the main is already archived and processes **only** extras.
- Autopilots (`prep_push_rep` / `prep_push_rep_season`) with `--extras` push **and** replace the extras.

## Card E — Rollback (CHANGE-GATE) — **CHOSEN: E1 (additive; main contract untouched)** ✅ confirmed
- Extras run as a **separate, independently-resumable per-file phase** that reuses `RollbackJournal` /
  `make_video_dummy` / `merge_video_files` / the `.partial`+rename push idiom — exactly like `rename_folder` (IMP-D17)
  reused the journal additively.
- The **existing main-content rollback contract is byte-for-byte unchanged**: journal format/durability (`fsync` +
  `os.replace`), PONR locations / `mark_point_of_no_return()`, the O-1/O-2 split, the `RollbackHardFail` contract, and
  the season resume-range messaging.
- Extras push = O-1 resumable per file (no PONR). Extras replace = the same atomic two-rename + per-file PONR +
  `RollbackHardFail(resume_cmd="fetch_restore <id> --fetchExtras")`, journal in the extra's own folder. Extras restore =
  merge-to-temp + `os.replace`-on-verify (IMP-R6).
- **Change-gate cleared by the user.** Any executor that finds a step would alter EXISTING rollback behavior must STOP
  and surface it (CLAUDE.md change-gate).

---

## Added requirement (2026-06-29) — Cross-session / cross-account resumable execution
Every step/execution must be resumable so any fresh session — including a different Claude account or machine — can
pick up exactly where work stopped and never re-decide or re-run a completed step. Mechanism: the feature branch + its
commit history + three tracked files under `docs/feature-extras/` (`PLAN.md`, `DECISIONS.md`, and the NEW `PROGRESS.md`
execution journal, updated + committed after every step). Full spec + resume protocol: see PLAN.md §"Execution
resumability & cross-session / cross-account handoff" and Step 0. Generalizing this to all tasks is OD-6 (deferred,
snapshot-gated).
