# MediaVault — ZCode Session Onboarding

> **What this is:** the orientation doc for **ZCode** sessions (the ZCode harness agent) working on
> MediaVault. Written 2026-09-06 after a full first-session read of the docs and code; every line
> number below was verified against the tree on that date.
>
> **Relationship to the other onboarding docs:** `docs/ONBOARDING.md` is the DeepSeek-Harness /
> generic-agent version and remains canonical — this file does NOT replace or edit it. Read both.
> This file adds: a ZCode-specific protocol, a verified-as-of-today `main.py` function map, and the
> first-session open questions. If a fact here contradicts `ARCHITECTURE.md`, ARCHITECTURE wins.

---

## 1. The project in one minute

MediaVault turns **Google Pixel phones' free "original quality" Google Photos backup into unlimited
cold storage** for huge ripped video (movies / TV / anime / sports, 4K REMUX, DV FEL, up to ~100 GB).

```
prep ──► (balanced split via mkvmerge) ──► push (adb → Pixel /sdcard/Media)
                                              │  phone's Photos app auto-uploads (out of band)
                                              ▼
                                     archived: local file swapped for a ~10 KB playable dummy
                                              │  user wants to watch
                                              ▼
        fetch (Selenium ← photos.google.com) ──► restore (mkvmerge --deterministic + SHA256 verify)
```

Everything is a `python main.py <verb>` call; there is no daemon. State is **four JSON files at
`C:\Media\library_{movies,series,anime,others}.json`** (outside the repo, never committed) plus
per-media sidecars (`uid`, `<short_id>.sha256`, `checksums/`).

**The user's end goal ("Couch-Vault"):** browse from the couch (Apple TV / Ugoos AM6B+ via
Jellyfin), select → background fetch → notified when ready → play → auto re-archive. Jellyfin-first
(webhooks + plugins), Emby = warm fallback (lifetime owned), Plex = do-not-buy. The only genuinely
new component is **mvdaemon** (IMP-S2); the web console worker is its seed. Full roadmap:
`improvements/ROADMAP_END_GOAL.md`.

**Hardware/account context (why the design is what it is):** Alienware PC (always-on), 4× Pixel 1 /
1 XL (the unlimited-upload path, one per Google account: movies / TV / anime / Others-sports),
Ugoos AM6B+ (CoreELEC) + Valerion VisionMaster Pro 2 + KEF Q650c/Q750×2/Q350×4/STARK SW15 surround,
LG C1 65", Apple TV 4K 128 GB. Playback quality target: best DV/FEL print where available — this is
why the pipeline is REMUX-preserving and verify-or-bless about hashes (bytes must round-trip
exactly).

---

## 2. Active code map (verified 2026-09-06)

| File | Lines | Role |
|---|---:|---|
| `main.py` | 10,679 | The CLI hub. All verbs, split algorithm, rollback journal, TMDB enrichment, extras, web launch, argv dispatch. |
| `mainfetch.py` | 691 | Selenium Google-Photos fetcher (spawned as its own process). Profile routing + parallel trigger + hash-routed harvester. |
| `mvcommon.py` | 671 | Shared single-source-of-truth: library paths + I/O, hashing, config getters (`mvconfig.json`), token store, `retry()`, `fetch_session_lock`, `episode_num_from_id`. Never imports main/mainfetch. |
| `webui/server.py` | 1,029 | FastAPI ops console: serialized single-worker job queue wrapping `main.cmd_*`; token auth; `/api/items`, `/api/tree`, `/api/detail/{id}`, demo mode. |
| `webui/static/*.js` | 16 modules | No-build vanilla-ES-module PWA (tabs, dossier, grouped tree, progress ring, ⌘K palette, token auth). |
| `tools/` | 7 files | `migrate_lib.py`, `migrate_rehash_flag.py` (one-shots), `notify_toast.py`, `remux_unsplittable.py` (manual fix, never auto-invoked), `warm_profiles.py` + `.xml` (daily Chrome-session keep-alive), `tailscale_serve_setup.ps1`. |
| `tests/` | ~60 files | pytest suite (~768 tests last recorded) + `tests/smoke/` (80-case full-command cross-command gate). CI: `.github/workflows/ci.yml`. |
| `Master_Stream_Archiver*.py`, `MatchArchiver*.py` | — | **Standalone Tkinter apps — NOT part of MediaVault** (no shared imports/state). Quality-inspect + remux GUI (Ollama multi-agent review) and football-match chapterer. See `docs/STANDALONE_TOOLS.md`. Committed in PR #50 to remove the staged-but-uncommitted hazard. |
| `archive/` | — | Historical snapshots, never runtime. |
| `improvements/` | — | The backlog "brain": `PRIORITY.md` (what's next), `improvements_tier{A–H,R,S,U,X}.md`, `ROADMAP_END_GOAL.md`, research. |
| `docs/` | — | Per-feature plans/decisions + conventions; `docs/README.md` is the master index. |

Outside the repo: `C:\Media\library_*.json` (source of truth), `C:\Media\{Movies,Series,Anime,Sports}\`,
`C:\Media\Utils\ChromeProfile{,_TV,_Anime,_Others}\` (one signed-in Google account each),
`~/.mediavault/` (locks + logs).

### `main.py` internal map (grep-verified line numbers, 2026-09-06)

| Region | Lines | What's there and why |
|---|---|---|
| Imports + UTF-8 reconfigure | 1–41 | `mvcommon` imported both by-name AND as a module — the module-qualified calls (`mvcommon.web_host()`) are deliberate: tests monkeypatch the getters, and by-name bindings would capture stale values (the "binding hazard"). |
| Config constants | 43–170 | `REMOTE_ROOT`, `FFMPEG_PATH` (Emby's bundled ffmpeg), `DUMMY_MAX_BYTES=200_000`, `DUMMY_RECIPE_BY_EXT` (per-container dummy recipes), `DEVICE_ALIASES` (alias→ADB serial; `others` still `<NEW_PIXEL_SERIAL>` placeholder), `PARTIAL_SUFFIX`, `PUSH_VERIFY_REMOTE=False`, `CATEGORY_ROOTS`, and **`ENTRY_TYPE_KEYS`** (~166) — the authoritative entry-type registry (leaf / season_map / multi_ep_alias). |
| Utilities | 178–237 | `resolve_device`, `get_tech_specs` (MediaInfo deep scan), `parse_metadata_from_id` (naive 4-digit year). |
| Split & merge | 240–425 | `UNSPLITTABLE_CODEC_IDS={"A_FLAC"}` + `find_unsplittable_tracks` (mkvmerge -J pre-flight, IMP-C20), `split_video_file` (the **balanced-split** algorithm — pre-computes chunk count, asks for a softer per-chunk size +10 MB so keyframe drift never leaves a sliver chunk), `merge_video_files` (`+` append syntax), `refuse_if_unsplittable`, `_print_mkvmerge_failure` (IMP-C19: mkvmerge errors go to *stdout* and were being DEVNULL'd). |
| Deterministic rehash helpers | 425–535 | `bless_or_verify_merged_hash` — verify-or-bless (first restore of a split entry BLESSES the deterministic merged hash as canonical; later restores VERIFY and alarm pre-PONR). Disk pre-flight (`_free_space_ok` etc.: deferred 1X / eager 2X + max(1%,2GB) buffer). |
| Dummy system | 558–615 | `resolve_ffmpeg`, `make_video_dummy` (atomic temp+os.replace; per-container ffmpeg recipe: PCM silence 0.05 s for mkv/avi, 440 Hz AAC tone 0.5 s for mp4/mov — PCM is incompatible with ISO-BMFF). |
| **Auto-rollback** | 616–976 | The change-gated mechanism. Spec block (616–700), `RollbackHardFail` (705), `RollbackJournal` (717) — durable `.mediavault_txn.json` journal, record-before-act, fsync+os.replace, IMP-R7 leftover handling (auto-recover pre-PONR leftover / preserve post-PONR leftover), `_replay_inverses` LIFO, `recover_journal` (942), `cmd_recover` (976). **Only two true PONRs**: replace's commit-rename and restore's merged-chunk delete; push is O-1 resumable, prep fully reversible. |
| Local commands | 1038–1440 | `cmd_prep` (cloud-bearing early-skip guard, IMP-D4), set_search/poster/fanart/tmdb. |
| TMDB / online enrichment | 1330–3553 | TMDB client + caches, OMDb (`refresh_online`), EXA+GROQ trivia (`fetch_trivia`), `_gather_enrich_units` (skips the `other` category — sports is not on TMDB), `_write_nfo` (never raises, never `<tvdbid>`), `cmd_enrich_metadata` (2505), `_enrich_after_archive` (7627 — a deliberate isolated copy of the resolve/apply waterfall; must be kept in lockstep with `cmd_enrich_metadata`, nothing enforces this). |
| rename_folder | 3569–3800 | Crash-safe cascading folder rename via the rollback journal (os.rename = its own PONR); rewrites `folder_path` for every descendant. |
| Extras layer | 3808–4145 | `scan_extras_folders`, `merge_extras_into_title` (idempotent; dummy-sized files can never clobber cloud-bearing items), `_extras_item_paths` (the single path-composition seam). |
| Seasons / check / push | 4147–5440 | `cmd_prep_season` (SxxExx + anime + oth- positional numbering; multi_ep_alias creation), `cmd_check`, `write_remote_mvmeta` (disaster-recovery sidecar on the phone, best-effort), `_verify_chunk_hash` (C8, gated off), `push_one_extra`/`push_title_extras`, **`cmd_push`** (4735 — `.partial` + atomic `adb shell mv`, retry() 1/4/16s, resume-from-`_parts/`, no PONR/O-1), `_resolve_alias` (5150), `parse_push_group_args` (5259), `cmd_push_group` (5322). |
| Replace / verify | 5446–6448 | **`cmd_replace`** (5446 — the first PONR: `os.rename(original → .tobedeleted)`, two-rename atomic pattern), `cmd_replace_group`, `replace_one_extra`/`replace_title_extras`, `_status_disk_violation`/`_dangling_evidence` + `cmd_verify_library` (6011 — status-to-disk invariant, `possibly_dangling` detection), `cmd_repair_dummies` (6210), `cmd_verify_restore` (6368), `quarantine_restore_file` (6430). |
| Restore | 6449–7028 | **`cmd_restore`** (6449 — per-chunk pre-merge verify, quarantine, merge to `.merge_tmp`, verify-or-bless, PONR = merged-chunk delete), `cmd_restore_group`, `restore_one_extra`/`restore_title_extras`. |
| Reporting | 7029–7297 | `cmd_sort` (lang→year→size; relies on dict insertion order), `cmd_local_status` (greedy bin-packing), `cmd_scan_unprepped`. |
| Autopilots | 7298–8185 | `cmd_prep_push_rep` (7298), `cmd_prep_push_rep_season` (7355 — resume-range messaging), IMP-D22 enrich autopilots (7762 / 7882, `-tvdbid` refused, TTY-gated rename confirm — the only `input()` in the codebase), `cmd_dispatch_fetch` (8003 — Popen streaming, PYTHONIOENCODING=utf-8), `cmd_fetch_restore` (8051), `cmd_add_extras` (8100). |
| Web data layer | 8185–9239 | `classify_entry_state`, `guess_manual_id`, `suggest_target_folder`/`suggest_next_command`, `collect_reclaimable`, `items_payload`, `tmdb_detail` (cache-read-only `/api/detail`), tree builders, `resolve_artwork_path` (season-inheritance, realpath-contained). |
| Web launch + tokens | 9791–9917 | `cmd_web`, `cmd_token_create/list/revoke`. |
| Dispatch | 9922–10679 | Manual `sys.argv` if/elif walk per verb (no argparse — that's IMP-A2, deliberately pending). Unknown-token-tolerant token scanners; trailing value-keyword fail-fast arms (IMP-C14). |

### State machine

```
prep → local_ready ──push(all chunks)──► onboarded ──replace──► archived ──restore──► restored_local
        uploaded=False                    uploaded=True          (dummy on disk)          uploaded stays True
set_uploaded → force onboarded (rescue)      |  re-push restarts the cycle
```

---

## 3. Rules a ZCode session must not break

1. **Auto-rollback is change-gated.** Any change touching the journal format/durability, PONR
   locations, created-this-run scoping, `cmd_*` wrapping, `recover_journal` semantics, season
   resume messaging, or the `RollbackHardFail` contract → STOP and get an explicit user decision
   first (`CLAUDE.md`, ARCHITECTURE §12a, ROLLBACK_MECHANISM.md §10). IMP-C24 and IMP-R10 are
   *open precisely because they are gated on such a ruling*.
2. **`ENTRY_TYPE_KEYS` registry + guard test.** New/renamed entry type or shared field ⇒ update the
   registry (~main.py:166) and `tests/test_entry_schema_guard.py`; keep every whole-library
   iterator alias/season_map-safe (`_resolve_alias` or skip). Dereferencing `folder_path` on an
   alias is the PR #21 crash class.
3. **Gates before any PR touching `main.py`/`mainfetch.py`/`mvcommon.py`:** `pytest -q` AND
   `pytest tests/smoke -q` (fast full-command cross-command gate — run both, require both green).
4. **Never run two mutating MediaVault commands in parallel** — `load_library`/`save_library` have
   no lock (IMP-C24, Band 0, real incident: 13 corrupted entries + a dummy uploaded to Google
   Photos). This is the top open task.
5. **Tests never touch real `C:\Media` / `library_*.json`.** Use the conftest fixtures: `sandbox`
   (dual-patches `mvcommon.LIBRARY_*` AND `main.LIBRARY_*` — the binding hazard — plus LOCAL_ROOT),
   `sandbox_entry`, `sandbox_alias`, `sandbox_extras`, `mock_device` (stateful fake ADB device),
   `mock_fetch` (browser stub), `fake_dummy`, `fail_nth_subprocess`, `fail_merge`, `mock_tmdb`,
   `make_video` (>DUMMY_MAX_BYTES bytes). Windows glob gotcha: never rglob a bracketed name —
   `rglob("*.mkv")` + filter by `.name`.
6. **Human gates:** PR → STOP (merge to `main` is the user's); branch archive after merge is a
   separate user-approved step.
7. **Secrets:** never commit; credentials live in `C:\Users\harin\.claude\.env` (machine-global,
   outside every repo). `mvconfig.json` / `mvtokens.json` are gitignored; commit only
   `mvconfig.example.json`.
8. **Keep the priority system current:** any add/complete/re-prioritize updates
   `improvements/PRIORITY.md` + the tier file + `docs/priority-graph/priority-graph.html` in the
   same change. Practical operational answers get added to `docs/OPERATIONS_QA.md`.
9. **Consumer Impact Analysis** before changing any shared data contract: grep every consumer,
   verdict each safe/needs-fix, before coding.
10. **Karpathy discipline + secrets rule now apply through `~/.zcode/AGENTS.md`** (mirrored from
    `~/.claude/CLAUDE.md` 2026-09-06): think before coding, simplicity first, surgical changes,
    goal-driven execution.
11. **No silent handling:** a fundamental capability gap or contradiction → STOP and surface an
    explicit decision request instead of degrading quietly.

---

## 4. ZCode-specific protocol (this harness)

- **Agent framework provenance:** the repo was built with a Claude Code multi-agent pipeline
  (planner → orchestrator → executors by `[model:]/[effort:]` tags, git-agent, judge, optional
  multi-candidate worktrees + DECISION.md). Those `.claude/agents/*` files are **provenance, not a
  requirement** — do not port them. In ZCode, use ZCode's native agent tool / model routing for
  delegation; the *discipline* (plan → execute → verify → commit per step, human gates) still
  applies. The per-task artifact trail lives in `docs/feature-*/` (PLAN/DECISIONS/PROGRESS).
- **Global instructions:** `~/.zcode/AGENTS.md` (this machine's ZCode default) mirrors
  `~/.claude/CLAUDE.md`. Project rules live in the repo's `CLAUDE.md`; treat it as the workspace
  rules file even though ZCode reads `AGENTS.md` — the content is the contract.
- **First move in a fresh session:** this file → `docs/ONBOARDING.md` → `improvements/PRIORITY.md`
  (the 👉 NEXT pointer) → `ARCHITECTURE.md` section for whatever you touch. Grep by function name;
  never trust inline line numbers (this file's numbers were verified 2026-09-06 and will drift).
- **Editing `.claude/agents/*`:** snapshot the directory first; quote any YAML `description`
  containing `: `; never leave duplicate `name:` files under `.claude/agents/` (silent agent
  drop-out). (Kept here for completeness — ZCode sessions shouldn't need to touch them.)

---

## 5. Priority snapshot (2026-09-06 — re-check `improvements/PRIORITY.md`, it is the live source)

- 🔴 **IMP-C24** — concurrent library writes / lost updates. Top priority but 🚦 change-gated:
  needs the user's ruling among the candidate approaches in
  `docs/feature-library-concurrency/PLAN.md` (Open Decisions).
- 🔴 **IMP-C22** — `_episode_se_of` mis-parses season-glued anime ids (4th drifted copy of the
  episode parser; per-episode enrichment silently never lands for 145 anime entries). **Not gated
  — ready to implement.**
- 🟠 **IMP-D23** — `cmd_prep` re-hashes an already-prepped entry on every resume (the cost that
  pushed the user toward the risky parallel workflow that triggered C24's incident).
- 🟠 **IMP-R10** — spurious IRREVERSIBLE on a transient journal lock during replace's PONR write.
  🚦 change-gated, user deferred ("document more — can fix later").
- Then: **IMP-S1** (stand up Jellyfin — zero code, immediate couch value), **IMP-S2** (mvdaemon),
  IMP-A2→A5 (argparse/--json/config/logging chain), Tier X redundancy (CSAM-ban SPOF: X1 chunk
  replication first).

---

## 6. First-session open questions (for the user)

1. **"read.md"** — the user referenced the agent framework being described "according to read.md".
   No `READ.md` exists in the repo; the framework is documented in `.claude/AGENT_WORKFLOW_NOTES.md`,
   `ARCHITECTURE.md` §19, and the `.claude/agents/*` playbooks. Confirm that's what was meant
   (or point me at the file if it lives outside the repo).
2. **IMP-C24 ruling** — when ready, pick the concurrency-fix approach from
   `docs/feature-library-concurrency/PLAN.md` (Open Decisions). Until then IMP-C22 is the
   actionable Band-0 item.
3. **`DEVICE_ALIASES["others"]`** still holds the `<NEW_PIXEL_SERIAL>` placeholder — supply the
   Others Pixel serial when that device is in service.
4. **This doc's maintenance** — assumed protocol: ZCode sessions keep this file current in the same
   change whenever they add/change a command, entry type, or priority item (same rule as
   `docs/ONBOARDING.md`'s footer). Say the word if you'd rather it stay a point-in-time snapshot.

---

*End of ZCode onboarding. Canonical sources: `ARCHITECTURE.md` (engineering reference),
`README.md` (CLI reference), `docs/ONBOARDING.md` (component-interaction guide),
`improvements/PRIORITY.md` (what's next), `CLAUDE.md` (project rules).*
