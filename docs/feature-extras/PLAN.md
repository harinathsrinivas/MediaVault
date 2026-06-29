# Task: Add an `--extras` option (Specials / Trailers / Behind-the-Scenes) end-to-end — IMP-D19

Suggested branch: `feature/imp_d19_extras`

> Canonical plan folder: `docs/feature-extras/` (root `/PLAN.md` is the gitignored live copy; the identical
> tracked copy is `docs/feature-extras/PLAN.md`). The decisions log `docs/feature-extras/DECISIONS.md` will be
> written ONCE the Decision Cards below are answered (nothing is locked yet — see "Plan status").

## One-paragraph summary
Add a new **`--extras`** option to the prep/push family of commands that takes one or more folders (e.g. a season's
`Specials`, a show's `Extra`/`Behind the Scenes`, `Trailers`), recursively scans them for video files, hashes each with
the **same** `sha256` mechanism used for main content, stores them in a **separate nested `extras` block** on the
title's library entry (the season_map for series/anime/others, the movie leaf for movies), and pushes them to Google
Photos exactly like main content — with an **independent chunk size** (`--extras-size`, e.g. main at `SIZE_MB 5000`
but extras unchunked or at `9900mb`). Extras are **additive/idempotent** (add `Specials` now, `Trailers` later → same
end state as adding both at once), can be attached to **already-existing, archived, or local-only** titles, and on
**fetch / fetch_restore** a **`--fetchExtras`** flag (aliases `--fetch-extras`/`--extras`/`--extra`) fetches the extras
into their respective subfolders — **flag-only, no prompt**; flag absent = no extras. Restore recreates the original
`Specials`/`Extra` subfolder under the title so
Plex/Jellyfin/Emby keep recognizing them as extras. This is a brand-new addition tracked as **IMP-D19** (Tier D).

---

## ✅ Plan status: DECISIONS LOCKED — ready to execute (awaiting the user's "go")

All five Decision Cards were answered by the user on **2026-06-29** (authoritative record:
`docs/feature-extras/DECISIONS.md`):
**A2** (nested `extras` block GROUPED per source folder; group key = the extra folder's path relative to the title) ·
**B1** (dedicated `add_extras` command + `--extras`/`--extras-size` on the prep/push family) ·
**C → FLAG-ONLY (revised 2026-06-29)** (`fetch`/`fetch_restore` take a `--fetchExtras` flag — aliases
`--fetch-extras`/`--extras`/`--extra` — that fetches extras into their respective subfolders; NO prompt; flag absent = no extras) ·
**extras-size default = inherit the main split** · **D1** (full push→dummy→fetch→restore lifecycle) ·
**E1** (additive rollback; the existing main-content contract is byte-for-byte unchanged — change-gate cleared).
The only deviation from the planner's recommendation is **Card A → A2** (grouped per folder, not A1's flat rel_path
list); the A-dependent steps (1, 6, 7, 9) below have been revised to the A2 shape. **Execution has NOT started** — this
remains PLAN.md only (no code, no branches), pending the user's explicit go-ahead.

---

## Original task prompt
> I want to add another options, extras
> this can literally be any folder like speacials , trailers, behind the scenes etc.
> each time we prep or push - I want you to give an additional option
> like
> -extras "C:\Media\Series\English\Sci-Fi\Stranger Things\Stranger.Things.S01.2016.2160p.BluRay.HEVC.DD5.1 {tmdb-66732}\Specials"
>
> What this needs to do is , also hash all the videos inside the Specials folder in this case and keep it along with the main season or movie , then when fetching or fetching restore - code need to ask do you want to fetch with extras for this item (movie , series or anime) .
> By default the answer can be no - normal fetch doesnt restore all the extras unless specificed manually to use --fetchExtras
> Also it can be multiple folders separated with semi colon, like following
> "C:\Media\Series\English\Sci-Fi\Stranger Things\Stranger.Things.S01.2016.2160p.BluRay.HEVC.DD5.1 {tmdb-66732}\Specials;C:\Media\Series\English\Sci-Fi\Stranger Things\Stranger.Things.S01.2016.2160p.BluRay.HEVC.DD5.1 {tmdb-66732}\Trailers"
> Also the option should be additive , i.e. I should be able to first add Specials as a folder, then use again to add trailers etc. it should not be different from adding both folders as extra in first time itself.
>
> Also the code need to scan the entire folder structures given and scan for any video files like mkv, mp4, etc - and use the same hashing, and push for these files also.
> the information about these can stored in separate extras block in the json.
>
> You can already see there are a few folders like the stranger things - Specials, or
> C:\Media\Anime\Classic\Death Note (Complete Series) [1080p] (Dual Audio) {tmdb-13916}\Extra
> for Death note etc.
>
> Can also check these folder structure and files inside to decide how to implement this new option.
>
> Also, the new option should work perfectly in sync with the existing commands , and I should be able to add extras to already existing titles also  -- even archived or local ones.  All the other same split into chunks ,
> like size 9900 everything needs to be added for exrtras also - it can be different from the main chunking size also sometimes. i.e. main can be chunked with 5000mb, but extras can be not chunked or chunked with 9900mb . all these needs to be taken into account.
>
> If any decision pending, give me live example in real world usecase complete step by step and  ask me about the different options before you finalize the plan.
> Also, any other related improvements , how this approach will affect that can you eloborate. Also any prerequisite small task you want me to complete before we start this implemenation?
>
> If any decision or ambiguous question or any other approach related question - you can ask me first as decision card. Do not directly or blindly choose any option for this implemetnation.
> I want a full through plan - created using ultra code - or any number of agents or resource usage. DOnt worry about limits for this task. Come up with a complete - comprehensive plan - then we can start working on that first then once confirmed start executing.
>
> Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions".
> Note if we are solving any improvement tasks with this task say C18 for example - IMP-C18 needs to be marked done in improvements_tierC.md on implementation, the architect updates ARCHITECTURE.md/README (documented behavior change), and I want branch name, PR to main, and manual test commands at the end. Also you also need to update the priority graph and suggest me the next starts we can start.
> But since it is a brand new addition  of an option -  dont think it will affect any improvements.
>  you can delete existing plan.md  - that task should already be done and in PR also .

---

## Findings (codebase grounding)

### A. The real on-disk extras folders (inspected)
Both example folders place the extras as a **subfolder nested inside the title's main folder**, a sibling of the
episode files — exactly the Plex/Jellyfin/Emby convention:

- **Stranger Things** — title folder `C:\Media\Series\English\Sci-Fi\Stranger Things\Stranger.Things.S01.2016.2160p.BluRay.HEVC.DD5.1 {tmdb-66732}`
  - `Specials\` contains **2** files: `Stranger.Things.S01.Extras.Season.2.Trailer.….mkv.mkv` (**513 MB**) and
    `Stranger.Things.S01.Extras.The.Defenders.Trailer.….mkv.mkv` (**439 MB**). (Note the real `.mkv.mkv` double
    extension — `VIDEO_EXTENSIONS` `.endswith` still matches it.)
- **Death Note** — title folder `C:\Media\Anime\Classic\Death Note (Complete Series) [1080p] (Dual Audio) {tmdb-13916}`
  - `Extra\` contains **~19** files (Behind-the-Scenes, clean Openings/Endings, interviews), several **very large**:
    `Behind the Scenes - Japanese Voice Cast.mkv` is **2.66 GB**, others 1.1–1.3 GB. The whole `Extra\` folder is **~25 GB**.

**Design consequences proven by the data:** (1) extras are large enough that **splitting/chunking genuinely applies**
(the 2.66 GB / 513 MB files) and (2) **space reclaim matters** (Death Note `Extra\` alone is 25 GB) — so extras need
the **same push→replace-to-dummy→fetch→restore lifecycle** as main content, which is exactly why the user asks for
"fetch with extras" + `--fetchExtras` (you only fetch back something you dummied to reclaim space). (3) Restore must
**recreate the original `Specials`/`Extra` subfolder** so media servers keep recognizing them. (4) Right now these
extras are **invisible to the library and would already be reported as UNPREPPED** by `scan_unprepped` — after this
feature, once added, they must no longer be flagged (Consumer Impact #2/#3).

### B. Relevant existing code (file:line)
- **Library I/O** (`mvcommon.py`): `load_library` `mvcommon.py:551`, `save_library` (prefix-routes mov/tv/ani/oth)
  `mvcommon.py:569`, `calculate_file_hash` (the canonical sha256 + progress bar) `mvcommon.py:610`,
  `VIDEO_EXTENSIONS=('.mkv','.mp4','.avi','.mov')` `mvcommon.py:33`, `parse_size_str` `mvcommon.py:646`,
  `episode_num_from_id` `mvcommon.py:658`. **`extras` is nested inside entries already routed by prefix, so
  `mvcommon` needs NO change** (round-trips like `split_info`).
- **Schema authority**: `ENTRY_TYPE_KEYS` `main.py:144` (leaf/season_map/multi_ep_alias; `required`+`physical`).
  Optional nested blocks (`split_info`, `metadata`, `tech_spec`, `parent_id`) are NOT in the `required` sets — the
  `extras` block follows that precedent (no new entry type).
- **Hash/split/merge**: `get_tech_specs` `main.py:164`, `split_video_file` (SIZE_MB/SIZE_GB/COUNT balanced split, brace
  escaping) `main.py:221`, `merge_video_files` (`--deterministic` seed) `main.py:304`, `bless_or_verify_merged_hash`
  `main.py:359`, disk pre-flight helpers `_will_split`/`_required_extra_bytes`/`_free_space_ok` `main.py:384-443`,
  `make_video_dummy` `main.py:473`, `DUMMY_MAX_BYTES=200_000` `main.py:44`.
- **Rollback** (change-gated): `RollbackJournal` `main.py:623`, `recover_journal` `main.py:848`, `cmd_recover`
  (`--scan` walks `CATEGORY_ROOTS`) `main.py:882`, `RollbackHardFail` `main.py:611`, `TXN_JOURNAL_NAME` `main.py:608`,
  `REMOTE_ROOT="/sdcard/Media"` `main.py:43`, `PARTIAL_SUFFIX`/`write_remote_mvmeta` `main.py:88`/`main.py:3703`.
- **Commands the option touches**: `cmd_prep` (parent/season_map detection) `main.py:944` (auto-link `1024-1036`,
  season_map create `1043`); `cmd_prep_season` `main.py:3591`; `cmd_push` (split `3893-4020`, upload loop `4070-4157`,
  remote path math `3846-3850`) `main.py:3814`; `_resolve_alias` `main.py:4216`; `cmd_push_group` `main.py:4299`;
  `cmd_replace` (atomic two-rename, PONR `4481`) `main.py:4413`; `cmd_replace_group` `main.py:4555`; `cmd_restore`
  (merge-to-temp + `os.replace`, split `5065`, standard `5246`) `main.py:5046`; `cmd_restore_group` `main.py:5293`;
  `cmd_prep_push_rep` `main.py:5584`; `cmd_prep_push_rep_season` `main.py:5622`; `cmd_dispatch_fetch` `main.py:5785`;
  `cmd_fetch_restore` (season branch `5844`, single `5849`) `main.py:5826`.
- **Whole-library consumers** (Consumer Impact): `cmd_scan_unprepped` known_paths `main.py:5527-5531`;
  `cmd_local_status` skip `main.py:5415`; `collect_reclaimable` `main.py:6188`; `items_payload` `main.py:6386`;
  `build_tree` `main.py:6957`; `category_of_id` `main.py:6115`.
- **Fetch (`mainfetch.py`)**: `CHROME_PROFILES` `:32`, `profile_for_id` `:510`, `resolve_targets` `:410`,
  `build_download_queue` `:468`, `fetch_single_entry` `:254`, `cmd_fetch_route` `:517`, `parse_fetch_args` `:566`.
- **CLI dispatch** (`main.py:7564+`): per-command argv walkers for prep `7603`, prep_push_rep `7609`,
  prep_push_rep_season `7653`, prep_season `7757`, push `7796`, push_group (`parse_push_group_args` `4236`) `7849`,
  fetch `7872`, fetch_restore `7884`.
- **Tests/fixtures** (`tests/conftest.py`): `sandbox` (dual-patches LIBRARY_* incl. `lib_others`, redirects
  LOCAL_ROOT), `sandbox_alias`, `mock_device`, `mock_fetch`, `fake_dummy`, `make_video`, `stub_tech_specs`,
  `fail_nth_subprocess`, `mock_tmdb`; smoke style + `_seed_*` helpers in `tests/smoke/test_smoke_all_commands.py`;
  the schema guard `tests/test_entry_schema_guard.py`.

### C. IMP code
This is a **brand-new addition** (verified: no existing IMP covers extras/Specials — the only `extras`/`special`
hits in `improvements/` are incidental prose). Tier D runs through **IMP-D18** (done). Allocate **IMP-D19** (Tier D —
the data/library/content-command tier: D16 reclaim scan, D17 rename_folder, D18 Others category). On implementation it
**must** be registered in `improvements/improvements_tierD.md` + `improvements/PRIORITY.md` +
`docs/priority-graph/priority-graph.html` (the maintenance protocol at the bottom of PRIORITY.md), and flipped to
`done` in the PR.

### D. No external facts to pre-resolve
The feature reuses only internal primitives (mkvmerge `--split size:` / `--deterministic` merge already proven at v97;
`sha256` via `calculate_file_hash`; `adb push -p` + atomic `mv`). There is **no external API**, so executors will not
need a `DATA_REQUEST` for this feature.

---

## ✅ Decision Cards — ANSWERED 2026-06-29 (locked in `docs/feature-extras/DECISIONS.md`)

### Card A — Where the extras live + JSON shape + restore layout + de-dup identity — **CHOSEN: A2**
**Recommended was A1; the user chose A2 (nested block GROUPED per source folder).** Locked shape: `entry["extras"] =
{"groups": {"<group_rel>": {"added_date": "<YYYY-MM-DD>", "items": [{filename, sub_rel, short_id, hash, status,
uploaded, search_term, tech_spec, [split_info], [re_hashed]}, ...]}, ...}}` where `<group_rel>` is the extra folder's
path *relative to the title folder* (`Specials`, `Extra`, or a nested `Bonus/Trailers`) and `sub_rel` is the file's
path within that group. Restore recreates `<title>/<group_rel>/<sub_rel>`; de-dup is **per-group by `sub_rel`**. The
original A1/A2/A3 option descriptions are kept below as the historical record.

Real-world walkthrough (Stranger Things):
```
python main.py prep_season tv-en-2016-strangerthings-s01 "...\Stranger Things\Stranger.Things.S01... {tmdb-66732}" \
    --extras "...\Stranger.Things.S01... {tmdb-66732}\Specials"
```
- **Option A1 (RECOMMENDED):** the extras attach to the **title** entry — the `season_map`
  `tv-en-2016-strangerthings-s01` for a series/anime/others, the **movie leaf** for a movie — as a nested
  `"extras": {"items":[ ... ], "added_date": "..."}` block. Each item is identified by its **`rel_path`**
  (path relative to the title's `folder_path`), e.g.
  `Specials/Stranger.Things.S01.Extras.Season.2.Trailer.….mkv.mkv`, and carries
  `{rel_path, folder:"Specials", filename, short_id, hash, status, uploaded, search_term, tech_spec, [split_info], [re_hashed]}` —
  i.e. the **same leaf-shaped fields** so push/replace/fetch/restore reuse the existing logic per item.
  **Restore** recreates `<folder_path>/Specials/<file>` (the original subfolder) so Plex/Jellyfin keep seeing it as an
  extra. **De-dup identity = `rel_path`** (normalized): re-adding `Specials` later is a no-op for unchanged files;
  adding `Trailers` later appends new items → identical to adding both at once. Same basename in two folders
  (`Specials/intro.mkv` + `Trailers/intro.mkv`) = two distinct items (different `rel_path`). A file whose bytes changed
  (hash differs) updates the item and resets `uploaded=False` (needs re-push). **Bonus:** because `rel_path` is
  *relative*, `rename_folder` does not need to touch extras.
- **Option A2:** nested block but **grouped per source-folder** (`"extras": {"Specials":[...], "Trailers":[...]}`).
  Slightly nicer to read; marginally more code to flatten for push/fetch; de-dup is per-group. (No strong advantage.)
- **Option A3:** extras as **separate top-level leaf entries** (ids like `tv-...-s01-extra-01`) parented to the season.
  Reuses cmd_push/replace/restore verbatim, BUT changes the data contract much more (new id shape, pollutes the
  season's `children`/episode-range math, needs `ENTRY_TYPE_KEYS` work) and contradicts your "separate extras block in
  the json" wording. **Not recommended.**

Death Note example under A1: `Extra/Behind the Scenes - Japanese Voice Cast.mkv` (2.66 GB) → item
`{rel_path:"Extra/Behind the Scenes - Japanese Voice Cast.mkv", folder:"Extra", hash:..., split_info:{...}}` on the
`ani-…-deathnote` season_map; restore puts it back at `<show folder>/Extra/Behind the Scenes - Japanese Voice Cast.mkv`.

> **Answered (2026-06-29): A2** — nested block grouped per source folder (group key = path relative to the title);
> restore recreates the original `Specials`/`Extra` subfolder. See DECISIONS.md.

### Card B — CLI surface: flag names, which commands, dedicated `add_extras`, and the independent chunk-size flag/default
**Recommended: B1.**
- **Flags (mirror your notation):** `--extras`/`-extras "<f1>;<f2>"` — semicolon-separated AND repeatable
  (`--extras A --extras B`), both forms additive; `--extras-size`/`-extras-size <value>` for **independent** extras
  chunking, accepting `none` (unchunked), `9900mb`, `8gb`, or `SIZE_MB 9900` style; `--fetchExtras`/`--fetch-extras`
  (boolean) on fetch.
- **Accepted on:** `prep`, `prep_season` (scan+merge only — prep never uploads), `push`, `push_group`,
  `prep_push_rep`, `prep_push_rep_season` (scan+merge then upload). Plus a **dedicated `add_extras` subcommand** for the
  "add to an existing/archived title" case (see Card D):
  `python main.py add_extras <title_id> "<folders>" [--extras-size <v|none>] [device <id_or_name>] [no-replace]`.
- **`--extras-size` default (RECOMMENDED): inherit the main split** (same method/val as the main `SIZE_*` on that
  command); if the command has no main split, push extras **whole/unchunked**. `--extras-size` overrides per your
  "main 5000mb, extras 9900mb or none" requirement.

Walkthrough: `python main.py prep_push_rep_season tv-en-2016-strangerthings-s01 "...{tmdb-66732}" SIZE_MB 5000 --extras "...\Specials" --extras-size 9900mb device series`
→ main episodes split at 5000 MB, the two Specials split at 9900 MB (so each ~513/439 MB stays a single chunk), all
pushed to the *series* Pixel.

- **Option B2:** no dedicated `add_extras`; instead overload `push <title_id> --extras` to "just do extras" when the
  main is already archived. (Fewer commands, but conflates two intents; `push` on an archived title is otherwise an
  error.) 

> **Answered: B1** — dedicated `add_extras` + `--extras`/`--extras-size` on the prep/push family; `--extras-size`
> default = **inherit the main split**; flag spellings as written.

### Card C — `--fetchExtras` semantics — **REVISED 2026-06-29: FLAG-ONLY (no prompt)**
**Final decision: flag-only.** `fetch`/`fetch_restore` take a `--fetchExtras` boolean (aliases
`--fetch-extras`/`--extras`/`--extra`) that fetches each extra into its respective `Specials`/`Extra` subfolder; there
is **no interactive prompt**; default (flag absent) = no extras. The original prompt-based C1 description is kept below
as the historical record.

**(historical) Recommended: C1.** On `fetch <id>` and `fetch_restore <id>`, after resolving the title, if it has an `extras` block:
the **parent process (`main.py`) prompts once**: `This title has N extras (Specials, Trailers). Fetch them too? [y/N]`
— **default No** (Enter = No). `--fetchExtras` **skips the prompt and forces yes**. **Non-interactive** (no TTY — e.g.
the web worker, or piped stdin) **defaults to No** and never blocks. The resolved decision is passed to `mainfetch`
via the `--fetchExtras` argv flag (mainfetch itself never prompts — it is flag-driven, because it runs as a piped
subprocess). For a season, extras are **all-or-nothing** (the `episodes N-M` range filters only the *episodes*; extras
are gated solely by the prompt/flag).

Walkthrough: `python main.py fetch_restore tv-en-2016-strangerthings-s01 episodes 1-3`
→ fetches episodes 1–3, then asks `Fetch extras too? [y/N]` → Enter → extras skipped (only the 3 episodes restore).
`python main.py fetch_restore tv-en-2016-strangerthings-s01 episodes 1-3 --fetchExtras`
→ no prompt; episodes 1–3 **and** both Specials are fetched and restored into `…\Specials\`.

- **Option C2:** per-extra prompt (ask for each Specials/Trailers folder separately). More granular, more friction —
  not recommended for the default flow.

> **Answered (revised 2026-06-29): FLAG-ONLY (no prompt).** `fetch`/`fetch_restore` take `--fetchExtras` (aliases
> `--fetch-extras`/`--extras`/`--extra`); the flag fetches each extra into its respective `Specials`/`Extra` subfolder.
> No interactive prompt; default (flag absent) = no extras. Supersedes the original C1 prompt design.

### Card D — Adding extras to an EXISTING / ARCHIVED / LOCAL-ONLY title, and the replace(dummy) trigger
**Recommended: D1.** The fetch requirement proves extras must be **dummied to reclaim space** (you only fetch back what
you archived) — so extras get the **full push → replace(dummy) → fetch → restore lifecycle**, same as main content.
Triggers:
- **`add_extras <title_id> "<folders>"`** = scan+merge → push extras → replace extras to dummies — in one shot,
  **without touching the main content** (so it is the safe path for an **archived** title whose main file is already a
  dummy). `no-replace` keeps extras local (upload-only).
- **`push <title_id> --extras`** / **`push_group`** = upload extras (no dummy); a later `replace`/`replace_group`
  dummies them. The **autopilots** (`prep_push_rep` / `prep_push_rep_season`) with `--extras` do push **and** replace
  the extras (mirroring how they archive main content).
- On an **archived** main title, `push <id> --extras` detects the main is already archived and **only** processes
  extras (does not try to re-push the dummied main).

Walkthrough (archived Death Note, add its 25 GB `Extra/` later):
```
python main.py add_extras ani-ja-2006-deathnote "...\Death Note... {tmdb-13916}\Extra" --extras-size 9900mb device anime
```
→ scans the 19 files, hashes, splits the >9.9 GB ones, pushes to the *anime* Pixel, then replaces each with a tiny
dummy — reclaiming ~25 GB locally — all while the main episodes stay archived and untouched.

- **Option D2:** extras are **upload-only** (never dummied; main stays local). Rejected — it can't reclaim the 25 GB and
  makes "fetch extras" meaningless.

> **Answered: D1** — full push→dummy→fetch→restore lifecycle; `push --extras` on an archived title processes only
> extras.

### Card E — Rollback interaction (CHANGE-GATE — must be an explicit decision)
**Recommended: E1.** The extras upload/replace/restore runs as a **separate, independently-resumable phase** that
**reuses the existing `RollbackJournal` mechanism per extra file** but does **NOT** alter the existing rollback contract
for main content:
- Extras **push** is O-1 resumable per file (no PONR — the master survives; re-run resumes), exactly like `cmd_push`.
- Extras **replace** uses the same atomic two-rename + per-file PONR + `RollbackHardFail(resume_cmd="fetch_restore …")`
  pattern as `cmd_replace`, scoped to the single extra file (journal lives in that extra's folder, e.g. `…\Specials\`).
- Extras **restore** uses the same merge-to-temp + `os.replace`-on-verify (IMP-R6) pattern.
- The journal **format/durability (`fsync`+`os.replace`)**, the **PONR locations / `mark_point_of_no_return()`** of
  the existing `cmd_push`/`cmd_replace`/`cmd_restore`/season autopilot, the **O-1/O-2 split**, the **`RollbackHardFail`
  contract**, and the **season resume-range messaging** are all **byte-for-byte unchanged**. This is **additive** —
  the same way `rename_folder` (IMP-D17) reused `RollbackJournal` without changing the contract. Per CLAUDE.md /
  `ROLLBACK_MECHANISM.md §10`, this plan therefore does **not** trip the change-gate — but because extras introduce a
  new replace(PONR) path, you (the user) must confirm this is acceptable.

> **Answered: E1** — additive; the existing main-content rollback contract is byte-for-byte unchanged (change-gate cleared).

---

## Goal (verifiable definition of done)
Assuming the recommended options (A1/B1/C1/D1/E1):
1. `prep`/`prep_season`/`push`/`push_group`/`prep_push_rep`/`prep_push_rep_season` accept `--extras "<f1>;<f2>"`
   (repeatable) and `--extras-size <v|none>`; `fetch`/`fetch_restore` accept `--fetchExtras`; new `add_extras` works.
2. `--extras` recursively scans the given folders for `VIDEO_EXTENSIONS`, hashes each via `calculate_file_hash`, and
   merges them **idempotently** (dedup per-group by `sub_rel`) into the title's nested per-folder `extras` block —
   adding Specials then Trailers == adding both at once.
3. Extras push splits by `--extras-size` (independent of the main split), uploads via the proven `.partial`+rename+retry
   path to the mirrored remote subfolder, writes an mvmeta sidecar, and flips each extra `uploaded/onboarded`.
4. Extras replace dummies each uploaded extra (reclaiming space); extras restore re-merges + verifies + places the file
   back into the recreated `Specials`/`Extra` subfolder; statuses track `local_ready→onboarded→archived→restored_local`.
5. `fetch`/`fetch_restore` take a `--fetchExtras` flag (aliases `--fetch-extras`/`--extras`/`--extra`) that fetches each
   extra into its respective `Specials`/`Extra` subfolder; **no interactive prompt**; flag absent = no extras.
6. Works on **existing/archived/local-only** titles via `add_extras` (and `push --extras` skips an already-archived main).
7. **Cross-command integrity:** added extras are NOT mis-flagged UNPREPPED by `scan_unprepped`/`collect_reclaimable`;
   every whole-library iterator tolerates an `extras`-bearing entry; `recover --scan` finds extras-folder journals.
8. `ENTRY_TYPE_KEYS` adds NO new entry type (extras is an optional nested field, documented like `split_info`); the
   schema guard stays green with **added** round-trip coverage for an extras-bearing entry.
9. **No rollback-contract change** (E1). `pytest -q` AND `pytest tests/smoke -q` green, with new extras coverage.
10. **Cross-session / cross-account resumability:** the feature branch carries a committed
    `docs/feature-extras/PROGRESS.md` execution journal (updated + committed after every step); a fresh session — even
    on a different Claude account/machine — resumes from `PLAN.md`+`DECISIONS.md`+`PROGRESS.md`+git history without
    re-deciding or re-running any `done` step.

## Files affected
- `main.py` — new extras module (scan/merge/dedup helpers, `push_one_extra`/`replace_one_extra`/`restore_one_extra`
  + per-title drivers), CLI parsing for `--extras`/`--extras-size`/`--fetchExtras` + new `add_extras` dispatch,
  wiring into `cmd_prep`/`cmd_prep_season`/`cmd_push`/`cmd_push_group`/`cmd_replace`/`cmd_replace_group`/`cmd_restore`/
  `cmd_restore_group`/`cmd_prep_push_rep`/`cmd_prep_push_rep_season`/`cmd_fetch_restore`/`cmd_dispatch_fetch`, the
  consumer fixes (`cmd_scan_unprepped`/`collect_reclaimable`/`items_payload`/`build_tree`), and the `ENTRY_TYPE_KEYS`
  comment documenting the `extras` optional field.
- `mainfetch.py` — extras download queue from the title's `extras` block, `--fetchExtras` argv flag in
  `parse_fetch_args`, extras handling in `resolve_targets`/`cmd_fetch_route`.
- `mvcommon.py` — **no change expected** (extras nests in already-prefix-routed entries; round-trips like `split_info`).
  Only touch if a tiny shared helper (e.g. a video-recursion walker) is cleanly placed here.
- `tests/conftest.py` — new `sandbox_extras` fixture (binding-hazard-aware).
- `tests/test_extras.py` (NEW), `tests/test_entry_schema_guard.py` (round-trip an extras-bearing entry),
  `tests/test_cli_parsers.py` (extras token parsing), `tests/smoke/test_smoke_all_commands.py` (extras round-trip +
  alias-sweep tolerance + scan_unprepped not-flagged).
- `ARCHITECTURE.md`, `README.md`, `docs/README.md`, `BEST_PRACTICES.md`, `improvements/JELLYFIN_SETUP_GUIDE.md` — docs.
- `improvements/improvements_tierD.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html` — IMP-D19.
- `docs/feature-extras/` — `PLAN.md` (this, tracked copy) + `DECISIONS.md` (locked Card A–E) + `PROGRESS.md` (the NEW
  cross-session/account execution journal, Step 0) + completion report.

## Approach (end-to-end before the steps)
The feature is "teach the title entry to carry a nested, leaf-shaped list of extra videos, and run each extra through
the **same** push/replace/fetch/restore primitives the main content uses." Foundationally (Step 1) define the `extras`
block schema + a pure scan/merge/dedup core. Step 2 wires the CLI (the `--extras`/`--extras-size`/`--fetchExtras` flags
+ the `add_extras` command). The three lifecycle phases — **push** (Step 3, multi-candidate: refactor-for-reuse vs
isolated duplication of the upload core), **replace** (Step 4), **fetch+restore** (Steps 5–6) — each reuse the proven
split/hash/upload/dummy/merge primitives per extra, scoped so the main rollback contract is byte-for-byte unchanged
(E1). Step 7 closes the cross-command-integrity holes (scan_unprepped/collect_reclaimable/items_payload/build_tree must
know about extras so they aren't mis-flagged UNPREPPED — the PR#21 class of "a new shared field silently breaks a
distant command"). Step 8 documents the `extras` optional field in `ENTRY_TYPE_KEYS` and extends the schema guard.
Steps 9–11 add the fixture + unit + smoke coverage. Steps 12–14 update docs, register IMP-D19, and run the smoke gate
last.

## Execution resumability & cross-session / cross-account handoff (NEW REQUIREMENT — 2026-06-29)

**Requirement (user):** every step / execution must be **resumable** — any fresh Claude Code session, *including one
signed into a different Claude account or on another machine*, must be able to pick the work up exactly where it was
left and **never re-decide or re-run an already-completed step**.

**Where the state lives — git-tracked on the feature branch, so `git pull`/`checkout` carries it across accounts.** The
resumable state IS the feature branch: its commit history plus three tracked files under `docs/feature-extras/`:
1. `PLAN.md` — what to do (this file; the tracked copy, not the gitignored root live copy).
2. `DECISIONS.md` — the locked Card A–E choices, so a resumer never re-decides.
3. `PROGRESS.md` — the **execution journal** (NEW): the single machine-readable "where we are."

**`PROGRESS.md` schema** (updated AND committed at the END of every step, and whenever a step is paused mid-way):
- Header: task `IMP-D19`, branch, the locked-decisions one-liner, `Last updated`, and a **`▶ NEXT ACTION`** pointer
  (the exact next step / command to run).
- A **step table**: `Step | status (pending|in_progress|done|blocked) | completing commit SHA | tests (pass/fail + which) | notes`.
- For the multi-candidate Step 3: the candidate worktrees, the judge `DECISION.md` path, and the chosen candidate.
- A **sub-state** block for any `in_progress` step (what was done so far INSIDE it — e.g. "candidate B drafted + tests
  written, judge not yet run") so a resumer continues *inside* the step, not from its start.
- A **blockers** block for any `DATA_REQUEST` / human-gate / `RollbackHardFail` that paused execution.

**Resume protocol (what a fresh session/account does FIRST):**
1. `git fetch && git checkout feature/imp_d19_extras` (or create the branch from `main` if it does not exist — first run).
2. Read `PLAN.md` + `DECISIONS.md` + `PROGRESS.md` (in `docs/feature-extras/`). Do NOT re-run the planner.
3. Reconcile: `git log --oneline` must match the per-step SHAs in `PROGRESS.md`; `git status` must be clean. If they
   disagree, trust git history (the committed state) and correct `PROGRESS.md`.
4. Resume at the first non-`done` step: if `in_progress`, continue from its sub-state notes; else start the next
   `pending` step. **Never re-execute a `done` step.**
5. After finishing a step, update + commit `PROGRESS.md` (and tick the PLAN.md checkbox) in the SAME commit as the step.

**Granularity.** The committed unit of resumability is the PLAN step (each step ends in its own commit — existing
git-agent behavior). Finer mid-step resumability is covered two ways: (a) `PROGRESS.md` sub-state notes for the
in-progress step; (b) for the long *runtime* phases this feature adds (extras push/replace/restore), the per-file
`RollbackJournal` already makes the *operation itself* resumable on re-run (Steps 3/4/6, decision E1).

**Scope (deliberate).** This journal is **feature-local** (`docs/feature-extras/PROGRESS.md`) — NOT a revival of the
app-level session/task tracking that was intentionally removed in commit `dfd4067` (IMP-E16/A11). It is plain tracked
markdown and adds NO runtime/app behavior. Promoting it into a **standard convention for every future task** (a
`PROGRESS.md` per feature folder, referenced by `.claude/agents/orchestrator.md`) is offered as **OD-6** below — that
would edit `.claude/agents/` and is therefore snapshot-gated + a separate user decision.

## Steps

> Model/effort tags are advisory (executors run at their frontmatter effort; `opus`→max, `sonnet`→medium). Every
> code-touching step runs `python -m pytest tests/smoke -q` green BEFORE its commit. **After EVERY step, update + commit
> `docs/feature-extras/PROGRESS.md` (status + completing SHA + test result) and tick the PLAN.md checkbox in the SAME
> commit** (the resumability requirement above). The pipeline runs from the MAIN session (orchestrator.md as a playbook
> — do NOT launch `orchestrator` via `Task`).

- [x] 0. [model: haiku] [effort: low] Scaffold + commit the execution journal `docs/feature-extras/PROGRESS.md`.
  - Files: `docs/feature-extras/PROGRESS.md` (NEW).
  - Details: Create `PROGRESS.md` per the schema in "Execution resumability" above — the IMP-D19 header + locked-decisions
    line + `▶ NEXT ACTION`, the full Step 0–14 table all `pending` (Step 0 flips to `done` when this commits), and empty
    sub-state/blockers blocks. This is the FIRST step (before any code) so the cross-session/account resumable state
    exists from the very start. Committed on the feature branch by git-agent.
  - Acceptance: `PROGRESS.md` exists on the branch, lists every step, and names Step 1 as the NEXT ACTION; a fresh
    checkout + read of PLAN/DECISIONS/PROGRESS unambiguously says "do Step 1 next."

- [ ] 1. [model: opus] [effort: high] Extras data model + pure scan/merge/dedup core.
  - Files: `main.py` (new helpers near the data layer / `cmd_prep_season`; `ENTRY_TYPE_KEYS` comment `144-148`).
  - Details (Card A = A2 — GROUPED per source folder): Define the nested block grouped per source folder:
    `entry["extras"] = {"groups": { "<group_rel>": {"added_date": "<YYYY-MM-DD>", "items": [ {filename, sub_rel,
    short_id, hash, status, uploaded, search_term, tech_spec, [split_info], [re_hashed]}, ... ]}, ... }}` on the
    **title** entry (season_map for series/anime/others; movie leaf for movies), where `<group_rel>` is the extra
    folder's path **relative to the title's `folder_path`** (basename for the flat case — `Specials`, `Extra`; a nested
    case — `Bonus/Trailers`) and `sub_rel` is the file's path **within that group** (just the filename when the group is
    flat — both real samples are flat one level). `"groups"` wraps the per-folder map so an arbitrary folder name can
    never collide with a reserved key. Implement: `_extras_title_id(library, id)` → resolve a given id to its title id
    (an episode/alias → its `parent_id` season_map; a movie leaf → itself; uses `_resolve_alias`);
    `scan_extras_folders(folder_specs, title_folder_path)` → recursively walk each folder (`os.walk`, exclude
    `SPLIT_DIR_NAME`/`CHECKSUM_DIR_NAME`/`RESTORE_DIR_NAME`/`.git`/`.idea`/`__pycache__`), collect every
    `VIDEO_EXTENSIONS` file, derive `group_rel = os.path.relpath(folder, title_folder_path)` (fallback: folder basename
    if not under the title folder), `sub_rel = os.path.relpath(file, folder)`,
    `short_id = generate_short_id(title_id + "::" + group_rel + "/" + sub_rel)`, `hash = calculate_file_hash`,
    `tech_spec = get_tech_specs`; the item's on-disk/restore path = `<title folder_path>/<group_rel>/<sub_rel>`.
    `merge_extras_into_title(library, title_id, scanned)` → additive/idempotent merge: a new `group_rel` adds a group; an
    existing group merges items keyed by normalized `sub_rel` (new appended; unchanged skipped; changed-hash item updated
    + `uploaded=False`). Accept the `;`-split + repeated-flag list as input. Document the `extras` optional field in the
    `ENTRY_TYPE_KEYS` comment block (parallel to how `split_info` is described) — **do NOT** add a new entry type or
    change the `required` sets. Pure where possible (no upload here).
  - Acceptance: unit-level — scanning the 2 Stranger Things Specials yields a `Specials` group with 2 items (correct
    `sub_rel`/`hash`); calling merge twice is a no-op the second time; adding `Trailers` after `Specials` equals adding
    both at once (two groups); a changed-byte file flips that item's `uploaded` to False. opus: this is the shared data
    contract.

- [ ] 2. [model: sonnet] [effort: medium] CLI parsing for `--extras`/`--extras-size`/`--fetchExtras` + new `add_extras`.
  - Files: `main.py` dispatch (`7603`–`7895`), a new pure `parse_extras_tokens(tokens)` helper near
    `parse_push_group_args` (`4236`).
  - Details (Card B = B1; Card C = flag-only): Add `--extras`/`-extras` (collect ALL occurrences AND split each on
    `;`, strip quotes) and `--extras-size`/`-extras-size` (`none` | size-with-unit via `parse_size_str` → SIZE_MB, or
    `SIZE_MB/SIZE_GB/COUNT val`) to the argv walkers for `prep`, `prep_season`, `push`, `push_group`, `prep_push_rep`,
    `prep_push_rep_season`; thread them as new kwargs (`extras=None`, `extras_size=None`) into the cmd functions
    (default None = today's behavior, byte-for-byte). Add a `--fetchExtras` boolean (aliases `--fetch-extras`/`--extras`/`--extra`) to `fetch` /
    `fetch_restore` (flag-only — no prompt; absent = no extras). Add the new `add_extras` subcommand: `add_extras <title_id> "<folders>" [--extras-size <v|none>]
    [device <id_or_name>] [no-replace]`. Extract the shared `--extras`/`--extras-size` tokenizing into
    `parse_extras_tokens` so it is unit-testable (mirrors the `parse_push_group_args` precedent). Keep unknown tokens
    silently skipped (matches existing parsers); a value-taking flag with no value prints `❌ Error: Missing value …` +
    `sys.exit(1)`.
  - Acceptance: `parse_extras_tokens(["--extras","A;B","--extras","C","--extras-size","9900mb"])` → folders `[A,B,C]`,
    extras_size `("SIZE_MB","9900")`; the dispatch passes the kwargs through (covered in `test_cli_parsers.py`); existing
    parsers unaffected. `python -m pytest tests/test_cli_parsers.py tests/smoke -q` green.

- [ ] 3. [model: opus] [effort: max] [candidates: 2] Extras upload phase (reuse the push primitives; independent chunk size; resumable).
  - Files: `main.py` (new `push_one_extra(...)` + `push_title_extras(library, title_id, extras_size, device_id)`;
    wiring into `cmd_push`/`cmd_push_group`/`cmd_prep_push_rep`/`cmd_prep_push_rep_season`/`add_extras`). Read
    `docs/feature-auto-rollback/ROLLBACK_MECHANISM.md §10` first (change-gate).
  - Details (assumes Card E = E1): For each not-yet-uploaded extra item: compute the remote dir from the extra file's
    on-disk folder via `os.path.relpath(<extra folder>, LOCAL_ROOT)` (mirrors the `Specials`/`Extra` subfolder on the
    phone), split by `extras_size` (independent of the main split; `none` = whole-file push) using the existing
    `split_video_file`, hash chunks, upload to `<final>.partial`→atomic `mv` with `mvcommon.retry`, delete local chunk,
    write `extras` `split_info`, write a per-extra `write_remote_mvmeta`, then set the item `uploaded=True`,
    `status="onboarded"`. The phase is **O-1 resumable per file** (no PONR; a resume `_parts/` is never deleted). The
    main content's `cmd_push` journal/PONR/contract must remain **byte-for-byte unchanged** (E1) — confirm against
    `ROLLBACK_MECHANISM.md §10`; if any executor finds a change would alter the existing rollback behavior, **STOP and
    surface it** as a user decision. Disk pre-flight: reuse `_free_space_ok`/`_required_extra_bytes` per extra.
  - Acceptance: with `mock_device`, pushing a title carrying 2 extras lands the extra chunks at the mirrored remote
    subfolder, stores chunk hashes in the extras `split_info`, flips both items `uploaded=True`; re-running resumes a
    half-pushed extra; the existing `test_cmd_push_*` + smoke push tests stay green (main path untouched).
  - Judge criteria (most important first): (1) **correctness** — extras chunks land at the correct mirrored remote
    paths, hashes stored, `uploaded` flips, resumable on re-run, independent chunk size honored; (2) **blast radius on
    the proven `cmd_push` path** — the existing push/replace/restore + smoke tests stay green and `cmd_push`'s journal/
    PONR are unchanged (E1 / change-gate); (3) **rollback-contract safety** (main O-1/O-2 contract byte-for-byte
    intact); (4) **duplication vs single-source-of-truth** maintainability of the upload protocol.
  - Candidate approaches:
    - A: **Refactor for reuse.** Extract `cmd_push`'s per-file core (split → hash chunks → `.partial`+rename+retry
      upload → delete local chunk → mvmeta) into a shared `_upload_file(...)` that BOTH `cmd_push` (the leaf) and
      `push_one_extra` call — one source of truth for the upload protocol; `cmd_push` becomes a thin caller. Larger
      diff on the proven path, but no protocol duplication and future push fixes apply to extras automatically.
    - B: **Isolated duplication.** Add a leaner standalone `push_one_extra(...)` that re-implements only the needed
      upload steps (no `chunk_range`/eager-rehash/`tempdir` complexity), leaving `cmd_push` **byte-for-byte untouched**.
      Smallest blast radius on the battle-tested push path; accepts some protocol duplication (the `.partial`+rename+
      retry idiom appears twice).
  - **🚦 USER CANDIDATE CHECKPOINT (task-specific, added 2026-06-29 — OVERRIDES the orchestrator's default auto-merge):**
    after both candidates are implemented + committed to their candidate branches AND the judge writes `DECISION.md`,
    **do NOT auto-merge or commit the winner.** STOP and relay to the user the judge's full analysis — the recommended
    candidate, the per-criterion reasoning, each candidate's `CRITIQUE.md` highlights, test results, and the diff size
    on the proven `cmd_push` path. The user decides whether to accept the judge's pick or select the other candidate.
    Only after the user's explicit choice does the orchestrator merge THAT candidate (`MERGE_CANDIDATE_WINNER` +
    `ARCHIVE_CANDIDATES`), run the smoke gate on the merged result, update PROGRESS.md, and commit. (Step 3 is the only
    multi-candidate step in this plan.)

- [ ] 4. [model: opus] [effort: high] Extras replace (dummy) phase for space reclaim.
  - Files: `main.py` (new `replace_one_extra(...)` + `replace_title_extras(...)`; wiring into `cmd_replace`/
    `cmd_replace_group`/the autopilots/`add_extras`). Read `ROLLBACK_MECHANISM.md §10` first.
  - Details (assumes Card D = D1, Card E = E1): For each `uploaded` extra not yet `archived`, mirror `cmd_replace`'s
    exact pattern per file — `make_video_dummy` into a temp, atomic two-rename swap with the per-file PONR
    (`mark_point_of_no_return` after the original leaves its path), promote any eager canonical hash, set the item
    `status="archived"`; a post-PONR failure raises `RollbackHardFail(resume_cmd=f"fetch_restore {title_id} --fetchExtras")`.
    The journal lives in the extra's own folder (e.g. `…\Specials\`). The autopilots (`prep_push_rep`/`_season`) and
    `add_extras` (unless `no-replace`) call this after the extras push; `replace_group` on a title also replaces its
    extras. The existing `cmd_replace` contract is **unchanged** (E1).
  - Acceptance: with `fake_dummy`, replacing a title's extras turns each extra file into the dummy bytes and sets the
    item `status="archived"` (reclaiming space); the main `test_cmd_replace` + smoke replace tests stay green. opus:
    touches the replace/PONR pattern (rollback-adjacent).

- [ ] 5. [model: opus] [effort: high] Extras fetch (queue from the extras block; the `--fetchExtras` flag — no prompt).
  - Files: `mainfetch.py` (`resolve_targets` `410` / a new extras-queue builder, `parse_fetch_args` `566`,
    `cmd_fetch_route` `517`), `main.py` (`cmd_dispatch_fetch` `5785`, `cmd_fetch_restore` `5826`).
  - Details (Card C = FLAG-ONLY, revised 2026-06-29): **No interactive prompt.** `fetch`/`fetch_restore` accept a
    `--fetchExtras` boolean (aliases `--fetch-extras`/`--extras`/`--extra`) parsed in `main.py` and forwarded verbatim to
    `mainfetch` in the argv built by `cmd_dispatch_fetch`. When the flag is absent, extras are skipped (today's behavior,
    byte-for-byte). In `mainfetch`, `parse_fetch_args` learns `--fetchExtras`; when set, build a download queue from the
    title's `extras` block (each item/chunk by `hash`, into a `restore/` folder under the EXTRA's destination so restore
    can place it back into its respective `…\Specials\` / `…\Extra\` subfolder) and fetch them alongside the main
    targets via the existing `trigger_download`/harvester/hash-match loop. The `episodes N-M` range filters only
    episodes; extras are all-or-nothing (the flag fetches every group). Because there is no prompt, the web-worker /
    non-TTY path simply never passes the flag and never blocks.
  - Acceptance: with `mock_fetch`, `fetch_restore … --fetchExtras` downloads the extras (hash-matched) into the right
    restore location; without the flag, extras are skipped (no prompt) and nothing blocks. Anime fetch routing for
    `ani-`/profile selection unchanged.

- [ ] 6. [model: opus] [effort: high] Extras restore (merge-to-temp + verify + place into the recreated subfolder).
  - Files: `main.py` (new `restore_one_extra(...)` + `restore_title_extras(...)`; wiring into `cmd_restore`/
    `cmd_restore_group`/`cmd_fetch_restore`).
  - Details (Card A = A2, Card E = E1): For each extra with downloaded files in its `restore/` folder: verify
    each chunk/file hash against the stored extras `hash`/`split_info`; if split, `merge_video_files` into a
    `<target>.merge_tmp` then `os.replace` onto the recreated path `<title folder_path>/<group_rel>/<sub_rel>` (creating
    the `Specials`/`Extra` subfolder) ONLY after verify/bless passes (IMP-R6 pattern); set the item
    `status="restored_local"`. `cmd_fetch_restore` calls `restore_title_extras` after restoring episodes when extras
    were fetched. Quarantine a bad chunk like `cmd_restore` does. The existing `cmd_restore` contract is unchanged (E1).
  - Acceptance: a seeded archived extra with chunks in its restore folder restores to `<folder_path>/Specials/<file>`
    with a verified hash and `status="restored_local"`; the main `test_cmd_restore_quarantine` + smoke restore tests
    stay green.

- [ ] 7. [model: opus] [effort: high] Cross-command integrity: make the whole-library consumers extras-aware.
  - Files: `main.py` (`cmd_scan_unprepped` known_paths `5527-5531`, `collect_reclaimable` `6188`, `items_payload`
    `6386`, `build_tree` `6957`).
  - Details: After this feature, extras videos on disk ARE in the library (nested in the title's `extras` block) but the
    whole-library iterators only build their "known" set from leaf `folder_path`+`filename` (skipping season_map) — so
    extras would be **mis-flagged UNPREPPED** (the exact PR#21 "distant command silently breaks" class). Fix: when
    building `known_paths` (scan_unprepped) and the reclaim/disk sets (collect_reclaimable), also add every
    extras item path (`os.path.join(title folder_path, group_rel, sub_rel)`, normalized) across ALL groups — iterating
    ALL entries incl. season_map for the extras block ONLY (still skip season_map/alias for the physical-leaf deref). Optionally surface
    extras in `items_payload`/`build_tree` (an extras count/badge) — keep minimal. Confirm `recover --scan` already
    walks `Specials`/`Extra` (they are under `CATEGORY_ROOTS` and not in the exclude list) — no change needed; note it.
    Confirm `rename_folder` needs no extras change (paths are relative — `group_rel`/`sub_rel`).
  - Acceptance: a library with an extras-bearing title + the extra files on disk → `scan_unprepped` does NOT list the
    extras as unprepped; `collect_reclaimable` does not mis-badge them; every iterator completes without raising;
    `test_entry_schema_guard` (Step 8) green. opus: this is the cross-command risk the smoke gate exists for.

- [ ] 8. [model: opus] [effort: high] `ENTRY_TYPE_KEYS` doc + schema-guard round-trip coverage for an extras block.
  - Files: `main.py` (`ENTRY_TYPE_KEYS` comment `114-148`), `tests/test_entry_schema_guard.py`.
  - Details: Document the optional `extras` nested block (on leaf + season_map) in the `ENTRY_TYPE_KEYS` comment exactly
    as `split_info`/`metadata` are described — **no new entry type, no change to the `required` sets / `physical`
    flags** (the guard's `NON_PHYSICAL_TYPES == ["multi_ep_alias","season_map"]` assertion stays true). Extend the guard
    test: build a leaf AND a season_map carrying a representative `extras` block, assert they round-trip through
    `save_library`/`load_library` byte-for-byte, and assert the whole-library read commands (`cmd_scan_unprepped`,
    `cmd_local_status`, `cmd_sort`) still complete without raising on an extras-bearing library. Never touch real
    `C:\Media`/`library_*.json`. Run `python -m pytest` and fix failures before marking done.
  - Acceptance: `python -m pytest tests/test_entry_schema_guard.py -q` green; the registry diff adds only the comment
    (no new type). opus per the testing rules (schema-guard correctness trap).

- [ ] 9. [model: opus] [effort: high] conftest fixture `sandbox_extras`.
  - Files: `tests/conftest.py`. Read `docs/testing-strategy.md` first.
  - Details: Add a `sandbox_extras` fixture built ON TOP OF `sandbox` (inheriting the dual LIBRARY_* patch + the
    `C:\Media` hard-guard). Seed a title (a movie leaf OR a season_map) with a nested `extras` block holding one group
    (`Specials`) of 2 items and create the 2 real (>`DUMMY_MAX_BYTES`) extra files on disk under that `Specials/`
    subfolder of the title folder, with the stored `hash` matching (use the `make_video` factory). Yield the title id,
    the group/`sub_rel` pairs, the on-disk paths, and the underlying `sandbox` dict. Patch ONLY the bindings `sandbox` already patches (do not DIY LIBRARY_*).
    Document it in `docs/testing-strategy.md §4`. Never touch real `C:\Media`. Run `python -m pytest` and fix failures
    before marking done.
  - Acceptance: a trivial test using `sandbox_extras` loads the library and finds the extras block + real files under
    tmp_path (not `C:\Media`). opus per the testing rules (conftest binding hazard).

- [ ] 10. [model: sonnet] [effort: medium] Unit/command tests `tests/test_extras.py`.
  - Files: `tests/test_extras.py` (NEW). Read `docs/testing-strategy.md` first (fixtures: `sandbox`/`sandbox_extras`
    for library I/O; `mock_device` for push; `fake_dummy` for replace; `mock_fetch` for fetch; `make_video`).
  - Details: Cover (a) scan/merge/dedup idempotence (add Specials then Trailers == both at once; re-add = no-op;
    changed-byte file resets uploaded); (b) extras push lands chunks on `mock_device` + flips uploaded + honors an
    independent `--extras-size`; (c) extras replace turns each into a dummy (`fake_dummy`) + status archived; (d) extras
    restore re-merges + places into the recreated `Specials/` subfolder with verified hash; (e) `parse_extras_tokens`
    parsing; (f) `add_extras` on an archived-main title processes only extras; (g) `rename_folder` keeps extras valid
    (paths are relative — `group_rel`/`sub_rel`). Never touch real `C:\Media`/`library_*.json`. Run `python -m pytest` and fix failures before
    marking done.
  - Acceptance: `python -m pytest tests/test_extras.py -q` green.

- [ ] 11. [model: sonnet] [effort: medium] Smoke coverage for extras (round-trip + alias-sweep + not-flagged).
  - Files: `tests/smoke/test_smoke_all_commands.py`. Read `docs/testing-strategy.md` first.
  - Details: Add a `_seed_title_with_extras(sandbox, make_video)` helper (mirroring the existing `_seed_*` helpers)
    creating a season_map + 1 leaf episode + a 2-item `extras` block with real extra files under a `Specials/`
    subfolder. Add smoke cases: an **extras round-trip** (`add_extras`/push extras under `mock_device` → `replace`
    extras under `fake_dummy` → `fetch_restore … --fetchExtras` under `mock_device` — no crash + correct top-level
    effect), an **extras-not-unprepped** assertion (`scan_unprepped` does not list the extras), and ensure the existing
    **alias sweep** stays green with an extras-bearing library. Keep each smoke "no crash + correct top-level effect".
    Never touch real `C:\Media`/`library_*.json`. Run `python -m pytest` and fix failures before marking done.
  - Acceptance: `python -m pytest tests/smoke -q` green in < ~30s including the new extras coverage.

- [ ] 12. [model: opus] [effort: high] Architect: document the behavior change.
  - Files: `ARCHITECTURE.md`, `README.md`, `docs/README.md`, `BEST_PRACTICES.md`, `improvements/JELLYFIN_SETUP_GUIDE.md`.
  - Details (documented behavior change — keep edits surgical): `ARCHITECTURE.md` §5 (the new `--extras`/`--extras-size`
    options on prep/push family + the new `add_extras` command + `--fetchExtras` on fetch/fetch_restore), §6.3 (the new
    optional `extras` nested block on leaf + season_map — its shape (grouped per source folder), `group_rel`/`sub_rel`
    identity, lifecycle, independent chunking), and the cross-command-consumer note (scan/reclaim/items aware of extras). `README.md` CLI reference +
    a short "Archiving extras (Specials/Trailers/Behind-the-Scenes)" workflow + the `--fetchExtras` flag behavior (no prompt) +
    the independent `--extras-size`. `docs/README.md`: index the `docs/feature-extras/` folder. `BEST_PRACTICES.md`:
    a note on the recommended on-disk extras layout (a `Specials`/`Extra` subfolder under the title). 
    `JELLYFIN_SETUP_GUIDE.md`: note that the recreated `Specials`/`Extra` subfolders are recognized by Jellyfin/Plex/Emby
    as extras (filename-as-title; cite the 2026 "Specials/Extras/Behind The Scenes" local-extras convention).
  - Acceptance: docs accurately describe the option, the JSON block, and the fetch flag; `docs/README.md` indexes the
    feature folder. opus: cross-file documented-behavior change.

- [ ] 13. [model: sonnet] [effort: medium] Register IMP-D19 (tier file + PRIORITY.md + priority graph).
  - Files: `improvements/improvements_tierD.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`.
  - Details: Per the maintenance protocol at the bottom of PRIORITY.md, update all three together. tierD: add an
    `## IMP-D19: extras option (Specials/Trailers/Behind-the-Scenes archival)` block in the D17/D18 shape (Category
    `other`; Priority `high`; Files; Current behavior; Proposed change; Rationale; Goal; Effort `medium-large`; Risk
    `medium` — new shared nested field touches the whole-library iterators, guarded by the schema guard + smoke gate;
    no rollback-contract change; If skipped; Status `pending`, flipped to `done` by the PR/architect — mirroring D18).
    PRIORITY.md: add an IMP-D19 row to **Band 1** (user-requested, moderate-risk new-field plumbing), bump **Last
    updated**, refresh the `👉 SUGGESTED NEXT TASK` note (keep IMP-S1/S2 as the standing headline). Graph: add a `D19`
    node mirroring the existing tuple shape and an EDGE to `E14` (the web media UI could later surface extras) — keep
    the array valid JS.
  - Acceptance: IMP-D19 present + consistent in all three; the graph array stays valid.

- [ ] 14. [model: sonnet] [effort: low] Final verification + smoke gate (the cross-command gate, last).
  - Files: none (runs the suites).
  - Details: Run the Verification block below from the repo root using `python -m pytest`. Confirm the schema guard is
    green and added no new entry type. Fix any failure before the PR.
  - Acceptance: every Verification command green; `python -m pytest tests/smoke -q` (the FINAL gate) green in < ~30s.

## Risks and edge cases
- **`extras` mis-flagged UNPREPPED (the PR#21 class):** the load-bearing risk. The new shared `extras` field is read by
  push/fetch/restore but the whole-library *iterators* (scan_unprepped/collect_reclaimable) don't know about it → they
  would report the Specials/Extra files as unprepped/reclaimable. Step 7 + the smoke "not-flagged" assertion are
  required, not optional.
- **Extras on a season_map make it reference physical files.** `ENTRY_TYPE_KEYS` is unchanged (extras is optional/nested,
  like `split_info`), but iterators that **skip** season_map (local_status, sort, items_payload) will not surface extras
  — acceptable (they are not episodes); the only one that MUST change is the disk-walk known-set (Step 7).
- **Relative paths vs `rename_folder`.** Storing extras paths *relative* to the title folder (`group_rel` + `sub_rel`)
  means a folder rename does not break extras (no stored absolute path). Verify a Step-10 test that an extras-bearing
  title survives `rename_folder`.
- **Remote path math across volumes / not-under-LOCAL_ROOT extras.** `os.path.relpath(extra_folder, LOCAL_ROOT)` can
  raise across drives; reuse `cmd_push`'s `except: basename` fallback. The recommended layout (extras under the title,
  under `C:\Media`) avoids this; document it.
- **Change-gate (E1).** Adding an extras-replace introduces a new PONR path. The plan keeps the EXISTING contract
  byte-for-byte; any executor that finds a step would alter existing rollback behavior MUST STOP and surface it.
- **Large extras + disk space.** Death Note `Extra\` is 25 GB; splitting a 2.66 GB file needs transient 1X–2X room.
  Reuse the existing disk pre-flight (`_free_space_ok`) per extra; a `tempdir`-style redirect is **out of scope** for v1.
- **No fetch prompt (revised C = flag-only).** Extras fetch is purely flag-driven (`--fetchExtras`); there is no
  interactive prompt, so the serialized web worker (IMP-E12/S2) simply never passes the flag and cannot wedge. Smoke
  covers both the flag-on and flag-absent paths.
- **Idempotence/dedup edge:** same basename in two different groups = two distinct items — must NOT collide on
  `short_id` (seeded from `title_id + "::" + group_rel + "/" + sub_rel`, not the bare filename).
- **`.mkv.mkv` double extension** (real Stranger Things data): `VIDEO_EXTENSIONS` `.endswith` matches it; the stored
  `filename` keeps the double extension; chunk naming uses `os.path.splitext` once (mirrors existing behavior).

## Consumer Impact Analysis
Adding the optional nested **`extras`** shared field to **leaf + season_map** entries IS a shared-data-contract change.
`ENTRY_TYPE_KEYS` is the authority: **no entry type is added/changed** (extras is an optional nested block, precedent
`split_info`), so `ENTRY_TYPE_KEYS`'s `required`/`physical` sets and the guard's `NON_PHYSICAL_TYPES` assertion stay
unchanged — only the comment gains an `extras` note + the guard gains round-trip coverage (Step 8). Every consumer of
the affected shapes is enumerated below.

| # | Site | Line(s) | Access | Verdict | Why |
|---|------|--------|--------|---------|-----|
| 1 | `mvcommon.load_library` / `save_library` | 551 / 569 | generic dict round-trip, prefix-route by id | safe | `extras` nests inside entries already routed by mov/tv/ani/oth; round-trips like `split_info`; no routing/key-shape change |
| 2 | `cmd_scan_unprepped` known_paths | 5527-5531 | builds known set from leaf `folder_path`+`filename`, skips season_map/alias | needs-fix | extras videos live nested (often on season_map) → would be reported UNPREPPED; add extras item paths (`group_rel`/`sub_rel`) to known_paths — Step 7 |
| 3 | `collect_reclaimable` (web reclaim) | 6188-6214 | disk-walk classifies physical files vs library leaf set | needs-fix | extras files would classify UNPREPPED / be mis-badged; add the extras path set — Step 7 |
| 4 | `items_payload` (`/api/items`) | 6386-6414 | iterates leaves; `by_category` | safe (optional enhance) | extras not surfaced as items (no deref of `extras` → no crash); optional extras count/badge — Step 7 |
| 5 | `build_tree` (`/api/tree`) | 6957 | disk-walk folder tree; skips season_map before deref `folder_path` | safe | `Specials`/`Extra` already appear as on-disk folders; never derefs the `extras` key |
| 6 | `cmd_local_status` / `cmd_sort` (whole-lib iterators) | 5415 / 5352 | skip season_map/alias before deref physical keys | safe | extras nested on skipped types → never dereffed; no new KeyError/crash class |
| 7 | `ENTRY_TYPE_KEYS` + `tests/test_entry_schema_guard.py` | 144 / whole file | `required`/`physical` per type; round-trip + guard | safe (doc + coverage) | no new type; document `extras` optional field + ADD round-trip coverage — Step 8 |
| 8 | `_resolve_alias` | 4216 | alias single-hop | safe | extras unrelated to aliasing |
| 9 | `cmd_push` / `cmd_replace` / `cmd_restore` (leaf, main content) | 3814 / 4413 / 5046 | operate on a leaf id; journal/PONR contract | safe (E1) | unchanged for main content; extras handled by a separate per-file phase (Steps 3/4/6); contract byte-for-byte intact |
| 10 | `cmd_recover --scan` | 882-917 | walks `CATEGORY_ROOTS` for `.mediavault_txn.json`, excludes _parts/checksums/restore/.git/.idea/__pycache__/Utils | safe | extras journals live in `Specials`/`Extra` (under category roots, NOT excluded) → found; confirm in Step 7 |
| 11 | `mainfetch.resolve_targets` / `cmd_fetch_route` | 410 / 517 | builds the download queue from leaf/season children | needs-fix | must ALSO queue the title's `extras` items when `--fetchExtras` is set — Step 5 |
| 12 | `cmd_rename_folder` descendants | 3429 | rewrites stored `folder_path` for descendants under a folder | safe | extras store paths RELATIVE to the title `folder_path` (`group_rel` + `sub_rel`), so a rename leaves them valid (no stored absolute path); add a Step-10 regression test |
| 13 | `cmd_prep` season_map auto-create | 1043 | creates the season_map a prep links to | safe | the extras block attaches to that same season_map; Step 1's `_extras_title_id` resolves an episode → its parent |
| 14 | `category_of_id` / web `_category_of` | 6115 / server | id-prefix → category | safe | extras carry no new id (nested under the title id); category unchanged |
| 15 | `tests/conftest.py` `sandbox` (LIBRARY_* dual-patch) | 28-116 | redirects all libs incl. `lib_others` + LOCAL_ROOT | safe (extend) | no new library file; a new `sandbox_extras` fixture builds ON TOP — Step 9 |

Every grepped consumer of `load_library`/`save_library` and the leaf/season_map shapes appears above with a verdict;
each `needs-fix` row names its fixing step. The new `extras` key has the consumers in #2/#3/#11 (the disk iterators +
fetch) — none was found "with zero consumers".

## Cross-cutting guards (stated explicitly)
- **No rollback-contract change (E1).** Extras reuse `RollbackJournal`/`make_video_dummy`/`merge_video_files`/the
  `.partial`+rename push idiom per file, but the EXISTING `cmd_push`/`cmd_replace`/`cmd_restore`/season-autopilot
  journal format/durability, PONR locations, O-1/O-2 split, `RollbackHardFail` contract, and season resume-range
  messaging are **byte-for-byte unchanged** (additive, like `rename_folder`/IMP-D17). If any executor finds a step
  would alter rollback behavior, STOP and surface it as a user decision (CLAUDE.md change-gate).
- **`ENTRY_TYPE_KEYS` adds no type.** Extras is an optional nested field; the guard stays green with only added
  coverage (Step 8).
- **Smoke gate is the final gate.** This plan touches `main.py` and `mainfetch.py`, so `pytest tests/smoke -q` is the
  last Verification line.

## Tests
- **Unit / command** (`tests/test_extras.py`, Step 10): scan/merge/dedup idempotence; push (mock_device, independent
  chunk size); replace (fake_dummy); restore (merge + recreated subfolder); `parse_extras_tokens`; `add_extras` on an
  archived-main title; `rename_folder` keeps extras valid.
- **Schema guard** (`tests/test_entry_schema_guard.py`, Step 8): a leaf and a season_map carrying an `extras` block
  round-trip byte-for-byte; whole-library read commands tolerate an extras-bearing library.
- **CLI parser** (`tests/test_cli_parsers.py`, Step 2): `--extras` (repeat + `;`), `--extras-size` (`none`/unit/SIZE_*),
  `--fetchExtras`.
- **Smoke** (`tests/smoke/test_smoke_all_commands.py`, Step 11): extras round-trip (push→replace→fetch_restore
  `--fetchExtras`), extras-not-flagged-UNPREPPED, alias sweep stays green with an extras-bearing library.
- **Fixtures:** new `sandbox_extras` (Step 9); reuse `sandbox`/`mock_device`/`mock_fetch`/`fake_dummy`/`make_video`.
- Every test obeys: never touch real `C:\Media` or real `library_*.json`; run `python -m pytest` and fix failures
  before marking the step done.

## Verification (run from repo root; use `python -m pytest`, never bare `pytest`)
1. `python -m pytest tests/test_extras.py -q` — scan/merge/dedup + push/replace/restore + add_extras unit tests pass.
2. `python -m pytest tests/test_entry_schema_guard.py -q` — green; registry diff is the comment only (no new type).
3. `python -m pytest tests/test_cli_parsers.py -q` — extras token parsing pinned; existing parsers unaffected.
4. `python -m pytest tests/test_cmd_push_partial.py tests/test_cmd_push_mock_device.py tests/test_cmd_replace.py tests/test_cmd_restore_quarantine.py -q` — main push/replace/restore unchanged.
5. `python -m pytest -q` — full suite green.
6. `python -m pytest tests/smoke -q` — **MANDATORY FINAL GATE** (cross-command). This plan touches `main.py` and
   `mainfetch.py`, so per the SMOKE-GATE rule this is the last gate before the plan is done; must be green and complete
   in < ~30s, including the new extras round-trip + not-flagged coverage.
7. **Resume dry-run (cross-session/account):** on a clean checkout of `feature/imp_d19_extras`, reading
   `docs/feature-extras/` `PLAN.md`+`DECISIONS.md`+`PROGRESS.md` plus `git log --oneline` unambiguously identifies the
   next step with no re-derivation, and every per-step SHA in `PROGRESS.md` matches `git log`.

## Manual test commands (the real Stranger Things / Death Note walk — touches REAL library/ADB/Selenium)
Run against the editions you intend to archive, or stop after the scan/"Filtered to N" lines. Assumes recommended
options.
1. **Scan currently flags the extras as unprepped (before adding):**
   `python main.py scan_unprepped` → the 2 `Specials\*.mkv` and 19 `Extra\*.mkv` appear as unprepped.
2. **Prep a season + add its Specials in one go:**
   `python main.py prep_season tv-en-2016-strangerthings-s01 "C:\Media\Series\English\Sci-Fi\Stranger Things\Stranger.Things.S01.2016.2160p.BluRay.HEVC.DD5.1 {tmdb-66732}" --extras "C:\Media\Series\English\Sci-Fi\Stranger Things\Stranger.Things.S01.2016.2160p.BluRay.HEVC.DD5.1 {tmdb-66732}\Specials"`
   → the season_map gains an `extras` block with 2 items; `scan_unprepped` no longer lists the Specials.
3. **Additive: add Trailers later (same end-state as adding both at once):**
   `python main.py add_extras tv-en-2016-strangerthings-s01 "C:\Media\Series\English\Sci-Fi\Stranger Things\Stranger.Things.S01.2016.2160p.BluRay.HEVC.DD5.1 {tmdb-66732}\Trailers"`
   → appends Trailers items; re-running the same command is a no-op (idempotent).
4. **Independent chunking — main 5000 MB, extras 9900 MB — full autopilot:**
   `python main.py prep_push_rep_season tv-en-2016-strangerthings-s01 "...{tmdb-66732}" SIZE_MB 5000 --extras "...{tmdb-66732}\Specials" --extras-size 9900mb device series`
   → episodes split at 5000 MB, Specials at 9900 MB; both pushed + replaced (dummied).
5. **Add 25 GB of extras to an ALREADY-ARCHIVED anime title, unchunked:**
   `python main.py add_extras ani-ja-2006-deathnote "C:\Media\Anime\Classic\Death Note (Complete Series) [1080p] (Dual Audio) {tmdb-13916}\Extra" --extras-size 9900mb device anime`
   → scans 19 files, splits the >9.9 GB ones, pushes to the anime Pixel, replaces each with a dummy (reclaims ~25 GB);
   the main episodes stay archived/untouched.
6. **Fetch WITHOUT extras (default — no flag):**
   `python main.py fetch_restore tv-en-2016-strangerthings-s01 episodes 1-3` → restores episodes 1–3 only; extras
   skipped (no prompt).
7. **Fetch WITH extras (flag):**
   `python main.py fetch_restore tv-en-2016-strangerthings-s01 episodes 1-3 --fetchExtras` → episodes 1–3 AND both
   Specials fetched + restored into `…\Specials\`. (`--fetch-extras`/`--extras`/`--extra` work as aliases on fetch.)
8. **Integrity:** `python main.py verify_library` (no status drift on the extras) and `python main.py local_status`
   (pending extras appear pre-push, are gone post-replace).

## Prerequisites for the user (before/at execution)
1. **Answer Decision Cards A–E above** (execution is blocked until then).
2. Confirm the **on-disk layout convention**: extras live in a subfolder under the title (`Specials`/`Trailers`/`Extra`/
   `Behind the Scenes`). Your existing folders already follow this — no moves needed.
3. The Others/sports Pixel serial (`<NEW_PIXEL_SERIAL>` from IMP-D18) only matters if you push `oth-` extras — not a
   blocker for movies/series/anime extras.
4. Nothing else — the feature reuses your existing accounts/profiles/ADB devices and the mkvmerge/ffmpeg binaries
   already on disk.

## Related improvements & impact
- **IMP-E14 (web media-type SPA):** extras could later be surfaced in the UI (an "Extras" sub-row per title); Step 7
  optionally adds an extras count to `items_payload`. Graph edge `D19→E14`.
- **IMP-X1/X2 (replication / topology):** extras chunks are additional cloud objects on the same accounts — the same
  CSAM-ban single-point-of-failure applies; replication (X1) should include extras chunks when it lands.
- **IMP-E1/E2 (subtitle pre-extraction / preview variant):** unrelated content types; no overlap, but both write
  sidecar/derived files into the title folder — confirm no filename collisions with the extras `restore/`/`_parts/`.
- **IMP-D17 (rename_folder):** extras store relative paths (`group_rel` + `sub_rel`), so rename_folder needs no extras
  change — a clean interaction (asserted in Step 10).
- **Auto-rollback (R-tier):** extras reuse the journal additively (E1) — no contract change; forward rollback work (R4/
  R8/R9) is unaffected.
- **IMP-S1 (Jellyfin):** Step 12 notes that restored `Specials`/`Extra` subfolders are recognized as extras by
  Jellyfin/Plex/Emby (local-extras convention) — extras Just Work in the media server.

## Branch & PR
- **Branch:** `feature/imp_d19_extras` (per `docs/git-pr-conventions.md`: type `feature`, lowercase, underscores, < 50).
  Canonical plan folder: `docs/feature-extras/`.
- **PR title (MUST include the IMP code):** `feature: add --extras option (Specials/Trailers/Behind-the-Scenes) end-to-end — IMP-D19`
- **PR body order** (per `docs/git-pr-conventions.md`): (1) the auto-generated Claude Code summary FIRST
  (Summary / Changes / Test plan); (2) a `## Original task prompt` section containing the **COMPLETE VERBATIM** original
  task prompt (reproduced above); (3) the `🤖 Generated with [Claude Code](https://claude.com/claude-code)` trailer.
- The smoke gate (`python -m pytest tests/smoke -q`) must be green before the PR is opened.
- **Checkpoint 0 — candidate checkpoint (human-gated, task-specific):** at the multi-candidate Step 3, after the judge
  writes `DECISION.md`, STOP and relay the judge's analysis + recommendation; do NOT auto-merge. The user picks the
  candidate to merge; only then proceed. (See the 🚦 USER CANDIDATE CHECKPOINT bullet under Step 3.)
- **Checkpoint 1 (human-gated):** STOP after creating the PR; do NOT `gh pr merge`/merge/push to `main` — ask the user.
- **Checkpoint 2 (human-gated):** after merge, ask before archiving; on approval create an annotated
  `archive/feature/imp_d19_extras` tag (merge info + revive steps), push it, delete the branch (local + remote).
- On implementation, the architect flips **IMP-D19 → done** in `improvements/improvements_tierD.md` + `PRIORITY.md`
  (move to ✅ DONE, bump the count) + the graph node, keeping all three in sync.

## Open Decisions (deferred / non-blocking — after Cards A–E are answered)
- **OD-1 — extras `tempdir` redirect:** allow redirecting extras `_parts/` to another volume (parity with the main
  `tempdir`) for the 25 GB Death Note case. Out of scope for v1; revisit if disk pressure bites.
- **OD-2 — web UI surfacing:** an "Extras" sub-view per title in the SPA (IMP-E14). Deferred; Step 7 leaves the data
  available.
- **OD-3 — per-extra fetch granularity:** Card C2 (ask per Specials/Trailers folder) if the all-or-nothing default
  proves too coarse.
- **OD-4 — extras for `oth-`/sports:** works mechanically (extras are category-agnostic), but sports rarely have
  extras; pushing `oth-` extras needs the Others Pixel serial (IMP-D18 prerequisite).
- **OD-5 — recursive nesting depth:** the scan recurses arbitrarily; if a `Specials/` ever nests sub-subfolders, the
  `sub_rel` preserves them within the group on restore — confirm with the user whether deep nesting is expected (today both samples are
  flat one level deep).
- **OD-6 — generalize the execution journal to ALL tasks:** promote the per-feature `PROGRESS.md` (this task's
  cross-session/account resume mechanism) into a standard agentic-workflow convention referenced by
  `.claude/agents/orchestrator.md` (a `PROGRESS.md` in every feature folder). Deferred + a separate user decision
  because it edits `.claude/agents/` (snapshot-gated per CLAUDE.md) and is broader than IMP-D19. For now the journal is
  feature-local to `docs/feature-extras/`.

## Suggested next tasks (after IMP-D19 ships)
Re-read `improvements/PRIORITY.md`; the standing order is unchanged:
1. **IMP-S1** — stand up Jellyfin (`JELLYFIN_SETUP_GUIDE.md`); zero code, immediate couch value — and it now also
   recognizes the extras restored by this feature.
2. **IMP-S2** — mvdaemon (the web worker is its seed).
3. **IMP-A2 → A5** config chain (argparse + `--json` + full mvconfig migration) — would also let `--extras-size`
   defaults live in config.
4. **IMP-X1/X2** — multi-account replication (now also covering extras chunks).
