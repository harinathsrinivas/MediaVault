# MediaVault — Onboarding / Component Interaction Guide

> **Read this first.** A self-contained orientation for any new agent or human session
> (including a DeepSeek Harness session) that needs to understand *what* MediaVault is,
> *how* its pieces interact, and *which rules must not be broken* — without re-deriving
> everything from scratch.
>
> **Canonical sources this summarizes** (read them for full detail):
> - `ARCHITECTURE.md` — the definitive engineering reference (data model, every command, rollback §12a, config §14, known issues §16).
> - `README.md` — user-facing overview + full CLI reference table.
> - `docs/README.md` — master index of ALL documentation.
> - `improvements/` — the backlog + direction "brain" (start at `improvements/README.md` → `PRIORITY.md` → `ROADMAP_END_GOAL.md`).
> - `CLAUDE.md` — project rules (human gates, change-gate, keep-PRIORITY-current rule).
>
> **⚠️ Stale-line-number caveat.** `ARCHITECTURE.md` cites `main.py` at "3081 lines" and many
> inline `main.py:NNN` references from 2026-06 era. `main.py` is now **~10,680 lines**; several
> deep-dive sections are point-in-time. **Prefer `grep` by function/constant name over trusting
> inline line numbers** (this is the doc's own stated caveat). Everything in *this* file was
> verified against the current tree.

---

## 1. What this is (30-second primer)

**MediaVault** is a single-user, Windows-only CLI that turns a **Google Pixel phone's free
"original quality" Google Photos backup into unlimited cold storage** for huge ripped video
files (movies, TV, anime, sports) — files up to 100 GB in 4K REMUX / DV-FEL, stored off local
disk and off Google Drive quota.

The core 5-verb lifecycle:

```
prep ─► (split) ─► push ─► replace ─► (phone & Google Photos auto-upload out-of-band)
                                                    │
                     fetch (Selenium ← Google Photos) ─► restore (merge + verify, back on disk)
```

- **`prep`** — index a local file: SHA256 it, extract MediaInfo tech specs, write sidecars (`uid`, `<short_id>.sha256`), create a library-JSON entry.
- **`push`** — optionally split into balanced ~10 GB chunks with `mkvmerge`, `adb push` to `/sdcard/Media` on the Pixel; each chunk uploads as `<name>.partial` then atomically `mv`s to its final name. The phone's Photos app then uploads to the cloud unattended (MediaVault is **not involved** in that upload).
- **`replace`** — once `uploaded` is true, swap the original for a **tiny ~10 KB valid playable video dummy** to reclaim disk while keeping Plex/Emby/Jellyfin entries intact.
- **`fetch`** — Selenium drives a per-account Chrome profile to search + download the file/chunks back from `photos.google.com`.
- **`restore`** — re-merge chunks (`mkvmerge --deterministic`), **SHA256-verify**, put the file back.

Every multi-step command is wrapped by **one** auto-rollback mechanism (see §3.8), so a failure
never leaves an undocumented half-finished state.

**Hardware / accounts (no secrets here):** Alienware PC (always-on), 4× Pixel 1 / 1 XL (the
unlimited-upload path), Ugoos AM6B+ (CoreELEC) + Valerion projector + KEF surround, LG C1 65",
Apple TV 4K. Three content Google accounts (movies / TV / anime) + a fourth for sports/Others.
Credentials live in `C:\Users\harin\.claude\.env` — never committed.

---

## 2. Repo map — which code does what

Active runtime code (only these matter for behavior):

| File | Lines | Role |
|---|---|---|
| **`main.py`** | ~10,680 | The CLI hub. All local prep/push/replace/restore, the split algorithm, auto-rollback journal, sort/scan/status, TMDB enrichment, `extras`, the `web` server launch, plus dispatching `fetch` to mainfetch.py. |
| **`mainfetch.py`** | ~692 | The Selenium Google Photos fetcher (runs as a subprocess). Per-account Chrome profile routing + download harvester. |
| **`mvcommon.py`** | ~671 | Shared library I/O + hashing helpers **plus** config getters (`mvconfig.json`), the token store, `retry()`, and the `fetch_session_lock`. Imported by both entry points (IMP-A1). |
| **`webui/server.py`** | ~1,029 | FastAPI/uvicorn operations console — a serialized single-worker queue that wraps `main.cmd_*`; the **seed of the future `mvdaemon`**. |
| **`webui/static/`** | 16 JS modules | The cinematic PWA UI (tabs, dossier hover-preview, grouped tree, fetch progress ring, token auth, ⌘K palette). No build step — plain ES modules. |
| **`tools/`** | 7 utilities | `migrate_lib.py`, `migrate_rehash_flag.py`, `notify_toast.py`, `remux_unsplittable.py`, `warm_profiles.py`, `tailscale_serve_setup.ps1`, `mediavault_warm_profiles.xml`. |
| **`tests/`** | ~60 files | pytest suite + **`tests/smoke/`** (the fast full-command cross-command gate). |
| **`docs/`** | per-feature plans/decisions | The readable provenance record (see `docs/README.md` master index). |
| **`improvements/`** | backlog | `PRIORITY.md` (what's next), tier A–H/R/S/U/X tasks, `ROADMAP_END_GOAL.md`, research. |
| **`archive/`** | historical snapshots | NOT used at runtime — old `main.py`/`mainfetch.py` versions + legacy `index_file.py`. |

**Lives OUTSIDE the repo** (never committed):
- `C:\Media\library_{movies,series,anime,others}.json` — the four live library files (source of truth).
- `C:\Media\{Movies,Series,Anime,Sports}\` — media roots; `C:\Media\Utils\ChromeProfile{,_TV,_Anime,_Others}` — the four signed-in Chrome profiles.

**Key `cmd_*` functions in `main.py`** (verified current line numbers):

| Function | Line | Purpose |
|---|---|---|
| `cmd_recover` | 976 | Finish an interrupted rollback |
| `cmd_prep` | 1038 | Index a file → leaf entry |
| `cmd_set_search` / `set_poster` / `set_fanart` / `set_tmdb` | 1195 / 1206 / 1234 / 1262 | Manual overrides / artwork / TMDB id |
| `cmd_enrich_metadata` | 2505 | Local-first TMDB backfill (dry-run default) |
| `cmd_refresh_online` / `cmd_fetch_trivia` | 2785 / 3266 | OMDb ratings / EXA+GROQ trivia caches |
| `cmd_set_uploaded` | 3553 | Emergency force-mark uploaded |
| `cmd_rename_folder` | 3620 | Crash-safe cascading folder rename |
| `cmd_prep_season` | 4147 | Batch-prep a season folder |
| `cmd_check` | 4262 | Re-hash in place vs library |
| `cmd_push` | 4735 | Split + ADB push |
| `cmd_push_group` | 5322 | Push a season group |
| `cmd_replace` | 5446 | Original → dummy swap |
| `cmd_replace_group` | 5593 | Replace a season group |
| `cmd_verify_library` | 6011 | Integrity checks |
| `cmd_repair_dummies` | 6210 | Regenerate archived dummies |
| `cmd_verify_restore` | 6368 | Dry-run hash check of `restore/` |
| `cmd_restore` | 6449 | Re-merge + verify + place back |
| `cmd_restore_group` | 6702 | Restore a season group |
| `cmd_sort` | 7029 | Re-sort all libraries |
| `cmd_local_status` | 7082 | Pending-upload list |
| `cmd_scan_unprepped` | 7196 | Find unprepped files on disk |
| `cmd_prep_push_rep` | 7298 | `prep→push→replace` autopilot (1 file) |
| `cmd_prep_push_rep_season` | 7355 | Season autopilot |
| `cmd_prep_push_rep_enrich` | 7762 | `prep_push_rep` + TMDB enrich (IMP-D22) |
| `cmd_prep_push_rep_season_enrich` | 7882 | Season + enrich (IMP-D22) |
| `cmd_dispatch_fetch` | 8003 | Thin wrapper → spawns `mainfetch.py fetch` |
| `cmd_fetch_restore` | 8051 | `fetch` then `restore` |
| `cmd_add_extras` | 8100 | One-shot extras archival |
| `cmd_web` | 9791 | Launch the operations console |
| `cmd_token_*` | 9865+ | Mint/list/revoke web tokens |

**Two things flagged (not documented in ARCHITECTURE.md):**
1. **Uncommitted, git-staged** files: `Master_Stream_Archiver*.py` (9 files) + `MatchArchiver*.py` — a *separate* tkinter GUI ("Golden Standard" stream analyzer + football match chapter-splitter) that is **NOT imported by `main.py`**. Parallel effort feeding the sports/Others category. Resolution (commit / archive / fold in) is an open user decision.
2. **`mvcommon.py` has grown** beyond the 6 original helpers — it now also owns `mvconfig.json` getters (`web_host`, `tmdb_api_key`, `exa_api_key`, …), the token store (`mint_token`…`validate_token`), `retry()`, `fetch_session_lock`, and `episode_num_from_id`.

---

## 3. How the pieces interact

### 3.1 The four library JSONs + the merge/split model

`load_library()` (in `mvcommon.py`) reads **all four** files and merges them into one in-memory
dict keyed by manual ID. `save_library(data)` splits back by ID prefix and **rewrites all four
files atomically** (`tempfile.mkstemp` + `os.replace`):

- `mov-*` → `library_movies.json`
- `tv-*` → `library_series.json`
- `ani-*` → `library_anime.json`
- `oth-*` → `library_others.json`
- anything else → fallback to movies + a **stderr warning** (so a typo'd prefix can't vanish silently).

`load_library` is **loud** — a corrupt file prints `❌ CRITICAL … Refusing to continue` and
`sys.exit(1)` (the old silent-zero behavior was deliberately removed by IMP-A1).

> 🔴 **This is exactly the IMP-C24 hazard (Band-0, change-gated):** there is **no lock** around
> `load_library`/`save_library`, and every save rewrites all four files from one in-memory dict.
> Two concurrent mutating commands each hold a stale snapshot across a slow operation, and the
> later save silently wins — a real incident already cost 13 corrupted entries + one dummy uploaded
> to Google Photos. **Never run two mutating MediaVault commands in parallel in the same shell/PC.**
> Plan: `docs/feature-library-concurrency/PLAN.md`. This is the top-priority open task.

### 3.2 Manual ID conventions (which library an entry lands in)

First three chars select the library. Canonical shapes:
- **Movie:** `mov-<lang2>-<year>-<slug>` — e.g. `mov-en-2024-inception`, `mov-ta-2024-maharaja`
- **TV:** `tv-<lang2>-<year>-<slug>-s<NN>e<MM>` — e.g. `tv-en-2016-strangerthings-s01e03`; parent = same minus `eMM`
- **Anime:** `ani-<lang2>-<year>-<slug><EE>` (no `e`) — e.g. `ani-ja-2006-deathnote07`; parent strips trailing digits
- **Others (sports):** `oth-<sport>-<year>-<competition>-s01e<NN>` — e.g. `oth-football-2026-fifaworldcup-s01e01`. Tournament edition = one season; each match-half = one episode; a match = two adjacent episodes (`e01`+`e02`). `prep_season` numbers `oth-` files by **filename sort order**.

Half-episodes are floats (`…e16.5`). The `-sNN` segment is **required** for `oth-` so the episode
parser strips cleanly.

### 3.3 Three entry types (and the schema guard)

- **`leaf`** — the implicit type (a leaf has *no* `type` key): owns a physical file (`folder_path` + `filename`), plus `status`/`uploaded`/`hash`/`short_id`/`metadata`/`tech_spec`, optional `parent_id`/`split_info`, and optional `extras` (on a **movie** leaf).
- **`season_map`** — `type: "season_map"`: `folder_path` + `children` + `total_episodes`. Virtual (no file).
- **`multi_ep_alias`** — `type: "multi_ep_alias"`: only `{alias_of, parent_id}`. Virtual. A combined file (`S04E19E20.mkv`) is registered once as a leaf under the **lowest** episode; each extra episode number gets a thin alias.

`ENTRY_TYPE_KEYS` (in `main.py`, ~line 166) is the **authoritative registry** of these shapes.
> 🔴 Rule: any **whole-library iterator** (`.values()`/`.items()`) MUST skip non-physical types or
> resolve aliases via `_resolve_alias()` before touching `folder_path`/`filename`. Dereferencing
> those on an alias is the **PR #21 / IMP-C12 crash class** (`KeyError: 'folder_path'`). Enforced by
> `tests/test_entry_schema_guard.py` and the `tests/smoke/` alias sweep.

### 3.4 The state machine (`status` + `uploaded`)

Tracked independently:

```
cmd_prep            → status="local_ready", uploaded=False
cmd_push (success)  → status="onboarded",  uploaded=True
cmd_replace         → status="archived"    (dummy on disk; uploaded stays True)
cmd_restore         → status="restored_local"
cmd_set_uploaded    → force-override to onboarded (rescue)
```

Quirks: `cmd_push` only sets `uploaded=True` on **full** chunk success (a partial/chunk-range push
leaves state unchanged → resumable). `cmd_prep` short-circuits if the entry is already
cloud-bearing (`uploaded` truthy or status in `{onboarded, archived, restored_local}`) or the file
is dummy-sized (< `DUMMY_MAX_BYTES` = 200 KB).

### 3.5 Identity & search — the `short_id` system

- `short_id = md5(manual_id)[:6]` — deterministic, 6-char. Same id ⇒ same uid.
- **Embedded everywhere** so Google Photos' fuzzy search lands on exactly the right file:
  - chunk filename pattern `<base> [<short_id>].chunk.%03d.mkv`
  - remote single-file rename `<name> [<short_id>]<ext>`
  - `search_term` field (`<base> [<short_id>]<ext>`) — what mainfetch types into the search box
  - sidecar files `uid` and `<short_id>.sha256`
- **Hash-first routing:** filenames are hints; identity is SHA256. The fetch harvester hashes
  downloads and routes by hash to the right `restore/` entry.

### 3.6 The balanced-split algorithm (`split_video_file`)

Naive `mkvmerge --split size:N` leaves a tiny "leftover" final chunk. The balanced split
**pre-computes chunk count** and asks for a *softer* per-chunk size so the remainder folds back:

```
num_chunks   = ceil(total_size / limit)
split_size   = ceil(total_size_mb / num_chunks) + 10   # +10 MB keyframe-drift buffer
mkvmerge --split size:split_size
```

Same idea with `method=COUNT`. Each chunk is immediately SHA256'd into `checksums/<chunk>.sha256`
and `split_info.chunks[]` **before** any upload (so an interrupted push can resume against
verified hashes).

### 3.7 Push protocol — `.partial` + atomic `mv` + `.mvmeta.json` (rclone "chunker" pattern)

1. Compute remote target dir (cross-drive failsafe via `os.path.relpath`).
2. Handle `_parts/` resume (existing chunks → no re-split/re-hash).
3. For each chunk: `adb push -p <local> <remote>.partial` → `adb shell mv <remote>.partial <remote>` (atomic remote rename). Google Photos never indexes a `.partial` as complete.
4. Wrapped in `mvcommon.retry()` (1/4/16 s + jitter, 3 attempts, pre-retry `rm <remote>.partial`).
5. On full success (no chunk-range filter): write `<base> [<short_id>].mvmeta.json` sidecar (disaster-recovery mirror of `split_info`; best-effort, never fails the push), then `uploaded=True` / `onboarded`.

Post-push remote hash verify (`PUSH_VERIFY_REMOTE`) is gated **off** by default (needs IMP-A5 to toggle without editing source).

### 3.8 The auto-rollback mechanism (⚠️ **change-gated — read before touching**)

One mechanism for all multi-step commands — `RollbackJournal` (one per command/id), durable on-disk
at `<folder>/.mediavault_txn.json`:

- Records each intended mutation **before** performing it (fsync + `os.replace`).
- `mark_point_of_no_return()` writes a `crossed_ponr` marker; after it, failure raises
  `RollbackHardFail(state, reason, resume_cmd)` — the `resume_cmd` names an **existing** command.
- `rollback()` replays inverses LIFO on a reversible failure; `commit()` deletes the journal on success.
- `recover_journal()` + `python main.py recover [id|folder]` finishes an interrupted rollback after a crash.

**The two true Points-Of-No-Return** (the master/original file is the source of truth):

| Command | PONR | On failure |
|---|---|---|
| `cmd_prep` | none | roll back this-run entry/sidecars |
| `cmd_push` | **none (O-1, resumable)** | leave partial upload, print exact `push <id>` resume |
| `cmd_replace` | commit rename `os.rename(original, .tobedeleted)` (atomic two-rename pattern) | pre-PONR roll back; at/after → `RollbackHardFail` naming `fetch_restore <id>` |
| `cmd_restore` | split-path merged-chunk delete | pre-PONR roll back (merge staged to `.merge_tmp`); at/after → `RollbackHardFail` naming `fetch_restore <id>` |

> 🔴 **This is load-bearing and change-gated.** Any change touching the journal format/durability,
> PONR locations, created-this-run scoping, `cmd_*` wrapping, `recover_journal` semantics, season
> resume messaging, or the `RollbackHardFail` contract → **STOP, state exactly what differs, ask the
> user as an explicit decision** (see `CLAUDE.md` and
> `docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §10).

### 3.9 Deterministic re-merge & canonical hash (`re_hashed` / `merge_seed`)

Split files lose a verifiable whole-file hash after merge (a re-merged file ≠ original hash). Solved
by blessing a **canonical** hash via `mkvmerge --deterministic <seed>` where `seed = short_id`:

- **Deferred (default):** blessed at first `cmd_restore` (verify-or-bless in `bless_or_verify_merged_hash`). Later restores **verify** against it and alarm (before the PONR) on mismatch.
- **Eager (`rehash` token):** `cmd_push` merges once and blesses into `split_info.canonical_hash`, promoted into `entry["hash"]` at `cmd_replace`.
- Schema: `re_hashed` (top-level bool), `merge_seed`/`merge_tool`/`rehashed_at` (under `split_info`).
- A **new split** resets the canonical (`re_hashed=False`, drops old seed/tool/at). A **resume** of existing `_parts/` does not.
- **Disk pre-flight:** a push that would exceed free space stops *before* splitting (deferred 1X, eager 2X, + `max(1%, 2 GB)` buffer); optional `tempdir <path>` redirects `_parts/` to another volume.

### 3.10 The dummy system (`cmd_replace` / `make_video_dummy` / `cmd_repair_dummies`)

`replace` swaps the original for a **valid playable video** (not a text blob), so Plex/Kodi/thumbnailers
don't choke. Per-container ffmpeg recipe (`DUMMY_RECIPE_BY_EXT`, shared video `color=black 128x72`, `libx264`, `-b:v 50k`):

| Ext | Audio | Source | Duration | ~Size |
|---|---|---|---|---|
| `.mkv` | `pcm_s16le` | silence `anullsrc` | 0.05 s | ~9,672 B |
| `.avi` | `pcm_s16le` | silence `anullsrc` | 0.05 s | ~18,978 B |
| `.mp4` | `aac 64k` | 440 Hz tone | 0.5 s | ~6,650 B |
| `.mov` | `aac 64k` | 440 Hz tone | 0.5 s | ~6,701 B |

(PCM is incompatible with ISO-BMFF, so `.mp4`/`.mov` use AAC + a tone to give real entropy; `.avi`
uses PCM to avoid MP3 framing bloat.) `DUMMY_MAX_BYTES = 200_000` is the "is this a dummy?" sniff
threshold across `cmd_prep`/`cmd_check`/`cmd_repair_dummies`/`collect_reclaimable`.

### 3.11 Fetch & restore end-to-end

- `main.py fetch` → `cmd_dispatch_fetch` spawns `python mainfetch.py fetch <id> [episodes R] [--fetchExtras]`.
- `mainfetch.py` picks a Chrome profile by id prefix (`ani`→anime, `tv`→tv, `mov`→movies, `oth`→others), attaches Selenium to `:9222`, searches by `search_term`, triggers "download original" (Shift+D), and a harvester polls `~/Downloads`, hashes each new file, and routes it to the right `restore/`.
- Session keep-alive (`tools/warm_profiles.py` + a daily Task Scheduler entry) prevents Google Photos from logging out a profile; a logged-out session raises `SessionExpiredError` and aborts cleanly (IMP-C17 / C6).
- `restore` re-merges chunks (`--deterministic`), verifies SHA256, `os.replace`s onto the dummy only after verify/bless (IMP-R6: dummy never zeroed), and quarantines a bad file to `restore/quarantine/` so a re-fetch self-heals (IMP-C11).

### 3.12 Content categories & `extras`

- Four categories selected by id prefix (`mov`/`tv`/`ani`/`oth`), rooted by `CATEGORY_ROOTS` →
  `{"movies":["Movies"],"series":["Series"],"anime":["Anime"],"other":["Sports"]}` (list-capable —
  append `"Documentary"` later with zero walker code change).
- `--extras` / `add_extras` give Specs/Trailers/Behind-the-Scenes the same push→dummy→fetch→restore
  lifecycle, stored in an additive `extras` block on the title entry (grouped per source folder),
  with an independent chunk size and opt-in `--fetchExtras`.

### 3.13 The web console (`python main.py web`) and the `mvdaemon` seed

`webui/server.py` is a serialized single-worker queue wrapping `main.cmd_*`, serving the PWA UI at
`http://127.0.0.1:8765`. Auth is minted, expiring, revocable tokens (sha256-only stored) plus
genuine-local-admin (loopback, no proxy headers). This is the **seed** of the end-goal `mvdaemon`
(IMP-S2) — the only genuinely *new* component the couch-vault flow needs.

---

## 4. Best practices & guardrails (the rules that must not be broken)

1. **Auto-rollback is change-gated** (§3.8). Stop + ask before touching it.
2. **The two PONRs** are the only irreversible points; `replace`'s two-rename pattern and `restore`'s
   temp-merge are the safety properties that protect them.
3. **`ENTRY_TYPE_KEYS` registry** — update it (and its guard test) when adding/renaming an entry type
   or shared field; keep every whole-library iterator alias/season_map-safe.
4. **Never run two mutating commands in parallel** (IMP-C24 — the open Band-0 hazard).
5. **Smoke gate** — `pytest tests/smoke -q` before any PR that touches `main.py`/`mainfetch.py`/`mvcommon.py`
   (the fast full-command gate that catches "did I break another command"). Plus `pytest -q` full suite.
6. **Tests use copies, never real data** — the `sandbox` fixture redirects `LOCAL_ROOT` and the four
   `LIBRARY_*` into a temp tree; never touch real `C:\Media` files or `library_*.json`.
7. **Human-gated checkpoints** — never merge to `main`, and never archive a branch, without explicit
   user approval (CLAUDE.md Checkpoints 1 & 2).
8. **Secrets discipline** — never commit a credential; read from `C:\Users\harin\.claude\.env`;
   default repo visibility private; commit redacted `*.example.*` + gitignore the real one.
9. **Keep `PRIORITY.md` + the priority graph + the tier file all in sync** on any task change
   (maintenance protocol at the bottom of `PRIORITY.md`).
10. **Consumer Impact Analysis** — when a step changes a shared data contract (entry type/key/id
    shape/status value), grep EVERY consumer and verdict each safe/needs-fix *before* coding (the
    discipline that caught the PR #21 crash class).
11. **Generic skills** — this machine's global `~/.dsh/AGENTS.md` (and Claude's `~/.claude/CLAUDE.md`)
    carry the Karpathy "think-before-coding / simplicity-first / surgical-changes / goal-driven"
    discipline + the secrets rule. Apply them.

---

## 5. The end goal — "Couch-Vault"

From `improvements/ROADMAP_END_GOAL.md` — the target experience: *"not even come to my computer —
open the app on Apple TV / the projector, browse in Jellyfin, select → fetch in the background →
get told when ready → watch → auto-archive."*

```
Apple TV / Ugoos AM6B+ → Jellyfin (serves C:\Media) → mvdaemon → main.py/mainfetch.py (untouched core)
                                                    → Pixel push → Google Photos
```

- **Jellyfin-first** (webhooks + plugins + DisplayMessage); **Emby** = warm fallback (lifetime owned);
  **Plex = do-not-buy** (no plugin/virtual-item surface, and the price window passed).
- The daemon **REUSES CLI verbs** (`fetch_restore`, `replace`, `recover`) — it never reimplements.
- Phases S1→S8 (S1 = stand up Jellyfin, zero code; S2 = `mvdaemon`; S3 = in-client fetch+notify;
  S4 = grace-archive; S5 = smart prefetch). Tier X = CSAM-ban multi-account redundancy (X1 the real backup).
- **"Can I stream on the fly?"** — tiered: today background-fetch + notify; near-term chunk-1-early
  (S6); far/gated local proxy (S8); never direct TV↔Google (no API, verified).

---

## 6. Where to go next (canonical "brain")

1. `improvements/PRIORITY.md` — **what to do next** (always-current; the `👉 SUGGESTED NEXT TASK` pointer).
2. `ARCHITECTURE.md` — engineering reference for any code change.
3. `docs/README.md` — master index of every doc; `docs/OPERATIONS_QA.md` — "how do I actually do this".
4. The relevant `improvements_tier<X>.md`.

**Current Band-0 snapshot (as of this write):**
- 🚦 **IMP-C24** — concurrent library writes / lost updates (change-gated; **top priority**, needs a user ruling on the fix approach).
- **IMP-C22** — anime per-episode enrichment mis-parse (4th episode-parser copy, drifted; 145 entries; ready to implement, not gated).
- **IMP-D23** — `cmd_prep` re-hashes an already-prepped entry on resume.
- Then **IMP-S1** (Jellyfin stand-up, zero code), **IMP-C3** (`doctor`), the **A2→A5** argparse/config chain.

---

## 7. Cross-harness note (DeepSeek Harness vs Claude Code)

This project was **built with a Claude Code multi-agent pipeline** — `planner → orchestrator →
executors (opus/sonnet/haiku) → judge → git-agent` (defined in `.claude/agents/`), on Opus 4.8
effort tiers, with `PLAN.md` + `STATUS.md` + `DECISIONS.md` artifacts. That is **provenance, not a
requirement**: a new session should **not** attempt to port those agents.

**In this DeepSeek Harness**, use the harness's own agentic tools naturally:
- `subagent` / `subagent_fork` — bounded delegation (research, a scoped implementation, a review).
- `workflow` — multi-agent fan-out/phase orchestration when a task genuinely fans out.
- `ralph` — fresh-agent iterative execution (only if the user explicitly asks).
- `goal` — one long-running completion objective with automatic rounds.

The **project-scope rules** in `CLAUDE.md` still apply as *discipline* even without the Claude
agents: the auto-rollback change-gate, the smoke gate, the human merge/archive checkpoints, the
consumer-impact analysis, and the "surface fundamental contradictions rather than silently degrade"
rule. The generic global skills live at `~/.dsh/AGENTS.md` (harness) and `~/.claude/CLAUDE.md`
(Claude Code) and are identical in spirit.

---

*End of onboarding. If you change a command, entry type, config constant, or priority item and this
file now misstates it — update the relevant section here in the same change.*
