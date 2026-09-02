# Session handoff — 2026-08-27 → 2026-09-03

> **Read this FIRST in a new session.** It is the context bridge: what shipped, what broke, what is
> open, and the standing hazards that are easy to trip. The implementable fix plan is the sibling
> `PLAN.md`; the task registrations are in `improvements/`.

---

## 1. What shipped in this window

| IMP | What | Merged |
|---|---|---|
| **IMP-D22** | `prep_push_rep_enrich` + `prep_push_rep_season_enrich` — archive-then-TMDB-enrich autopilots | PR #47 → `e5e94aa` |
| **IMP-C23** | `_has_tmdb_token` made case-insensitive (uppercase `{TMDB-…}` was double-stamped) | PR #48 → `abc2333` |

Both branches archived as annotated tags (`archive/feature/imp_d22_prep_push_rep_enrich`,
`archive/fix/imp_c23_has_tmdb_token_ignorecase`) and deleted local+remote. Suite **887 passed**,
smoke **80**.

IMP-D22's Step 1 was a two-way bake-off; the rejected zero-duplication candidate is preserved at
tag `candidates/imp-d22/step-1/b-rejected`. Judge decision + both critiques live in
`docs/feature-prep-push-rep-enrich/decisions/`.

---

## 2. 🔴 THE INCIDENT — exact scenario that corrupted 13 entries

This is the reason IMP-C24 exists. Reproduce it mentally before touching library I/O.

### What the user did, step by step

1. `python main.py prep_push_rep_season tv-en-1994-friends-s03 "<season folder>" episodes 1-13`
   → **prep succeeded, push failed** (the ADB device was not connected).
   Entries left at `status="local_ready"`, `uploaded=False`, real files still on disk, hashes stored.
2. Not wanting to re-hash 13 UHD files, they resumed manually:
   `python main.py push_group tv-en-1994-friends-s03 episodes 1-13`
3. **In a SECOND shell, concurrently**, they ran `python main.py replace tv-en-1994-friends-s03e12`
   (and similar, per episode) to reclaim disk as each push finished.
4. Result: entries ended in mixed, wrong state — some `status=onboarded`, some `uploaded=False` —
   while all dummies were correctly on disk. The user hand-edited some fields trying to fix it.
5. **Days later**, by mistake, they re-ran `push_group tv-en-1994-friends-s03 episodes 1-13`.
   e01–e10 were skipped (`uploaded=True`), but **e11–e13 had lost `uploaded=True`**, so `cmd_push`
   uploaded the **9,672-byte DUMMY** to Google Photos as if it were the episode.
   The D4 integrity guard printed
   `⚠️ INTEGRITY: tv-en-1994-friends-s03e11 status=onboarded but on-disk=VIDEO_DUMMY`
   — **but only AFTER the upload had already happened.**

### Why it happened (verified mechanism, not speculation)

`mvcommon.load_library()` (~line 552) reads **all four** library JSONs and **merges them into one
dict**. `mvcommon.save_library(data)` (~line 569) splits that dict by id prefix and **rewrites ALL
FOUR files** on every call — whether or not that library changed.

The per-file write IS atomic (`tempfile.mkstemp` → `json.dump` → `os.replace`), so a torn/corrupt
file is impossible. **But there is NO LOCK anywhere** — grep for `flock`, `msvcrt.locking`,
`LockFile`, or any library-level lock: none exist.

So two concurrent processes lose updates:

```
push_group : load()  →  …long ADB push…  →  set e11.uploaded=True  →  save()
replace    :        load()  →  make dummy  →  set e12.status=archived  →  save()   ← STALE snapshot
                                                                          ↑ silently erases e11
```

Last writer wins, writing from a snapshot taken **before** the other process's change.

**Blast radius is the WHOLE library set, not one file.** Because `save_library` rewrites all four
files from the merged dict, a concurrent `replace` on a *series* entry can clobber a *movie* or
*anime* entry another process changed in the interim. In this incident the damage happened to stay
inside Friends s03 — X-Files s11 pushes were running in the same window and escaped. That was luck.

**Nothing warns the user.** No lock, no advisory message, no documentation that parallel runs are
unsafe.

### Damage assessment + repair (already done — do not redo)

- Audited all four libraries for status-vs-disk consistency: **14 inconsistent entries**, of which
  13 were Friends s03 e01–e13 and 1 (`mov-en-2013-coherence`, MISSING) is pre-existing and
  unrelated (it predates this by months; see `docs/feature-extras/FIXES_PROGRESS.md`).
- The stored `hash` on the affected entries was still the REAL file's hash, so a restore that
  grabbed the dummy would have **failed loudly**, not corrupted silently. The safety net held.
- The user deleted the 3 junk dummy uploads from Google Photos and **confirmed the cloud holds the
  correct real files for e01–e13**.
- I repaired the 13 statuses `onboarded` → `archived` with a guarded edit (only flipped when the
  on-disk file was genuinely dummy-sized AND `uploaded is True`).
  **Backup of the pre-fix library:** the scratchpad copy `library_series_pre_s03_statusfix.json`
  (session-scoped — will NOT survive into a new session).
- Post-fix re-audit: **zero** inconsistencies remain apart from the pre-existing `coherence` entry.

### The near-miss worth internalising

`push_group`'s skip test is exactly `if library[mid].get("uploaded") == True:` — **one flag, nothing
else.** It never consults `status` and never looks at what is on disk. `cmd_prep` has a secondary
safety net for exactly this class (`if os.path.getsize(filepath) < DUMMY_MAX_BYTES: skip`);
**push has none.** A dummy-size guard on the push path would have made this incident impossible.

---

## 3. Open work, prioritised

### Band 0 — silent-corruption bugs
| IMP | What | Notes |
|---|---|---|
| **C24** | **Concurrent library writes lose updates (no lock)** | The incident above. Blast radius = all four libraries. `PLAN.md` has the options + recommendation. **Currently the `👉 NEXT` pointer.** |
| **C22** | Anime per-episode enrichment never lands | `_episode_se_of` is a 4th copy of the episode parser that never delegated to `mvcommon.episode_num_from_id`. Affects all 145 anime entries. Show-level enrichment works; per-episode stills + overview/title silently do not. |
| **R10** | Spurious IRREVERSIBLE on a contended journal during `cmd_replace`'s PONR | Pre-existing, change-gated, user deferred it. |

### High
| IMP | What |
|---|---|
| **D23** | Prep re-hashes an already-prepped `local_ready` entry — makes resuming a failed push cost a full re-hash (75 GB for Baahubali, 13 UHD files for Friends). See `PLAN.md`. |

### Unregistered papercuts (found in live use, no IMP code yet)
1. **`-nfo` is not accepted** as an alias for `--nfo`. `-tmdbid`/`-tvdbid`/`-extras` all accept the
   single-dash form; `--yes`/`--no-rename`/`--nfo`/`--no-web` do not. The user hit this — the
   unrecognised token fell through into the filepath and produced a confusing "File not found:
   …mkv -nfo". **Also worth a guard: warn when an unmatched token starts with `-`.**
2. **`enrich_metadata`'s ambiguous report prints an unusable command.** It suggests
   `set_tmdb <unit_key> <id>`, but for a SHOW the unit key (e.g. `tv-en-1993-xfiles`) is a *derived*
   key, not a library entry — the command errors with "ID not found". And the real container
   (`…-s01`) is a `season_map`, which `set_tmdb` refuses. It should suggest an episode **leaf**.
3. **The failed-push resume hint says "or simply re-run this same command"** without noting that
   re-running re-hashes the whole file. For a 75 GB file that is a material cost — it should point
   at `push` directly when a hash already exists. (Overlaps D23.)

### Recurring theme worth naming
Three of the bugs above are **the same failure mode: duplicated parsers/predicates that drifted.**
IMP-C18 fixed duplicated episode parsers and created `mvcommon.episode_num_from_id` explicitly so
"the three copies can't drift again"; **C22** is a 4th copy that never got wired to it; **C23** was
two copies of the token predicate that diverged on a regex flag. C23's fix added a *drift pin*
(`test_has_tmdb_token_agrees_with_provider_token_re`) asserting the two predicates agree — that
pattern is the durable fix and should be reused whenever a duplicate is knowingly kept.

---

## 4. Operational state (user's real library)

- **X-Files: 202 episodes across 9 seasons, ZERO enriched** (`tmdb_id` is `None` on all of them).
  Blocked on EXA returning **HTTP 402 (out of credits)**, which kills both the enrich web-fallback
  and `fetch_trivia`. Workaround that needs no EXA: `set_tmdb <an episode leaf> 4087` per season,
  then `enrich_metadata <prefix> --apply --nfo`.
- **Per-season-year id convention is a trap.** X-Files ids embed each season's own year
  (`tv-en-1993-xfiles-s01` … `tv-en-2001-xfiles-s09`), so they form **9 separate enrich units** and
  artwork lands in each *season* folder with nothing at show level. Friends uses the **show year for
  every season** (`tv-en-1994-friends-s01/-s02/-s03…`), which collapses to **one unit** and puts
  artwork in the **show** folder via `_show_folder_of`'s `commonpath` branch. **Folder names may
  still carry the real per-season year — only the ID year matters.** Use the Friends convention
  going forward.
- **Disk is tight.** C: was down to ~5.6 GiB free at one point; D: ~106 GiB. Eager `rehash` costs
  **2× the file** (chunks + a full merge temp) vs 1× deferred, so it is not viable for the 75–78 GB
  remuxes. `tempdir <path>` redirects **both** the chunks and the eager merge temp, and the
  free-space check targets the tempdir volume (`main.py:4745`, `:4835`).
- `mvconfig.json` has all four API keys set (tmdb, omdb, exa, groq). OMDb works; **EXA is 402**.

---

## 5. ⚠️ Standing hazards — every session must know these

1. **The user's personal `Master_Stream_Archiver*.py` / `MatchArchiver*.py` files are STAGED but
   uncommitted in the index, and have been for weeks.** **Every commit MUST use an explicit
   pathspec** (`git commit -m "…" -- <paths>`). NEVER `git add -A`, NEVER a bare `git commit`.
   A git-agent once swept them into a commit; it was recovered only because nothing had been pushed.
   If they are ever parked in a stash: `git stash pop --index` (the `--index` flag is REQUIRED or
   the staged/untracked split is lost), and **keep an independent byte-level backup** — the stash
   round-trip silently converts them CRLF → LF on this repo (`.gitattributes` is `* text=auto
   eol=lf`).
2. **Never run two library-mutating commands concurrently** until IMP-C24 is fixed. Mutating:
   `prep`, `push`, `push_group`, `replace`, `replace_group`, `restore`, `set_tmdb`, `set_uploaded`,
   `rename_folder`, `enrich_metadata --apply`, and the `prep_push_rep*` autopilots. Read-only and
   therefore safe alongside: `local_status`, `check`, `verify_library`, `scan_unprepped`, dry-run
   `enrich_metadata`.
3. **Auto-rollback is change-gated** (`CLAUDE.md`). Any change to journal format, PONR placement,
   `recover_journal`, or the `RollbackHardFail` contract must be surfaced to the user as an explicit
   decision BEFORE implementation. `main.py` has exactly **3** `raise RollbackHardFail(` sites; keep
   it that way unless the gate is opened.
4. **Smoke gate before any PR:** `python -m pytest tests/smoke -q` (currently 80). **Documented
   hazard: a bare `pytest -q` collects nothing in this repo — always `python -m pytest`.**
5. **Never touch the real `C:\Media` or `library_*.json` from tests.** Use the `sandbox` fixture in
   `tests/conftest.py` (it dual-patches all four `LIBRARY_*` constants AND `LOCAL_ROOT`) and
   `make_video`. Do not hand-roll that patching.
6. **Checkpoints are human-gated.** Checkpoint 1 = merging to `main`. Checkpoint 2 = archiving a
   merged branch. Never do either without the user's explicit approval.
7. **IMP-D22 left a standing maintenance obligation** (also recorded in `ARCHITECTURE.md`):
   `_enrich_after_archive` duplicates `cmd_enrich_metadata`'s resolve waterfall, apply block, and
   API-key guard. **Keep them in lockstep.**

---

## 6. Where things live

| Path | What |
|---|---|
| `docs/feature-library-concurrency/PLAN.md` | The C24 + D23 fix plan (options, recommendation, steps, tests) |
| `docs/feature-prep-push-rep-enrich/` | IMP-D22: PLAN, DECISIONS (7 locked rulings), PROGRESS journal, `decisions/` |
| `improvements/PRIORITY.md` | Single source of truth for ordering; has the maintenance protocol at the bottom |
| `docs/priority-graph/priority-graph.html` | Visual twin of PRIORITY.md — **must be kept in sync** |
| `improvements/improvements_tier{C,D}.md` | C24 / D23 task text |
| `ARCHITECTURE.md` | The TMDB-for-everything convention; the duplication-sync obligation |
