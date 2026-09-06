# MediaVault — Operations Q&A

> **A living reference of practical "how do I actually do this" answers**, built from real questions
> asked during real archiving sessions. Distinct from `README.md` (what commands exist) and
> `ARCHITECTURE.md` (how the system is built) — this is **how to operate it day to day, and the
> traps that have actually bitten.**
>
> **Every answer here was verified against the code or the live library at the time it was written.**
> Where behaviour is a known bug rather than a design, the IMP code is named.
>
> **Maintenance:** when a question is asked and answered in any Claude session, add it here.
> See the protocol at the bottom.

**Last updated:** 2026-09-07 (IMP-U6 — `[tmdbid-…]` token convention, NFO-at-stamp default, migration tool + Plex NFO-agent setup)

---

## 1. Metadata enrichment

### What actually fetches metadata? There are three commands, not one.

| Command | Source | Writes to | Gives you |
|---|---|---|---|
| `enrich_metadata` | TMDB | **library JSON + files on disk** | `tmdb_id`, real title/year/overview, poster, fanart, season posters, episode stills, episode titles, NFO |
| `refresh_online` | OMDb | `mvonline.json` (cache) | IMDb / Rotten Tomatoes / Metacritic ratings, MPAA rating, runtime, awards, box office |
| `fetch_trivia` | EXA + GROQ | `mvextra.json` (cache) | 2–4 short, source-tagged trivia facts |

The last two are **read-only caches** feeding the web console's hover dossier. They are deliberately
kept out of `library_*.json` so a ratings refresh can never corrupt archive state.

**Order matters:** run `enrich_metadata` first. The other two key off `metadata.tmdb_id`, so they
skip any entry that hasn't been enriched yet.

### Why does `enrich_metadata` need `--apply` but `prep_push_rep_enrich` doesn't?

`enrich_metadata` is a **bulk** command — run bare it can touch hundreds of entries, so it is
**dry-run by default** and previews what it would change. `--apply` opts in.

The `*_enrich` autopilots target one title you named explicitly. Nothing to preview, so they always
apply. There is no `--apply` flag on them.

### Does the enrich leg of `prep_push_rep_season_enrich` only cover the episodes I archived?

**No — the archive leg is range-scoped, the enrich leg is not.**

- Archive: only the `episodes N-M` you asked for.
- Completion check: enrich runs only if **that range** finished archiving.
- Enrich scope: gathered by `base_id`, so it covers **every episode of that season already in the
  library** — including ones archived in earlier runs.

This is useful: archiving episodes 11–20 will retroactively enrich episodes 1–10 if they were never
enriched. Chunking a season across several runs costs nothing extra — TMDB responses are cached and
existing artwork is never overwritten.

### Sports / Others (`oth-`) — archives fine, enrich does nothing

Deliberate, not a bug (`main.py:2013`, IMP-D18). `_gather_enrich_units` skips `oth-` ids because
sports isn't on TMDB — enriching would mis-tag it, rename the real Sports folder with a bogus token,
and fetch wrong posters. You'll see `⚠️ enrich: no enrich unit found … — skipped`. Use plain
`prep_push_rep_season` for Others.

### Anime — show level works, per-episode does NOT (IMP-C22, open)

Anime resolves through TMDB's **TV** endpoints like any series, and show-level art + metadata land
correctly. But **per-episode stills and synopses never land**, for either real anime id shape:

```
_episode_se_of('ani-ja-2015-kurokosbasketball-s0324')  -> (3, 324)   # WRONG - swallows the -s03 digits
mvcommon.episode_num_from_id(same id, parent)          -> 24.0       # correct
```

Pre-existing, shared with `enrich_metadata`, tracked as **IMP-C22**. Nothing breaks; data is just
silently absent.

### Ambiguous match — the suggested fix command doesn't work for shows

When TMDB returns multiple candidates, enrich refuses to guess and prints
`-> resolve with: python main.py set_tmdb <unit_key> <tmdb_id>`.

For a **show**, that unit key (e.g. `tv-en-1993-xfiles`) is a *derived* key, not a library entry —
the command fails with "ID not found". And the real container (`…-s01`) is a `season_map`, which
`set_tmdb` refuses.

**Pin it on an episode leaf instead:**
```
python main.py set_tmdb tv-en-1993-xfiles-s01e01 4087
python main.py enrich_metadata tv-en-1993-xfiles --apply --nfo
```
`_unit_preset_tmdb_id` scans every id in the unit and uses the first it finds — one leaf is enough
for the whole season. (Unregistered papercut; see `docs/feature-library-concurrency/SESSION_HANDOFF.md` §3.)

### EXA returning HTTP 402

**Payment Required — the EXA account is out of credits.** It breaks two things: the enrich
web-search fallback (so hard/concatenated/regional titles go AMBIGUOUS instead of auto-resolving)
and `fetch_trivia` entirely. OMDb (`refresh_online`) is unaffected — different provider.

Workaround with no EXA: supply ids manually via `set_tmdb` on a leaf, or pass `--no-web`.

---

## 2. ID and folder conventions

### The format

```
<category>-<lang>-<year>-<slug>[-sNN][eMM]

mov-ta-2012-thuppakki                  movie
tv-en-1994-friends-s01                 season container (season_map)
tv-en-1994-friends-s01e01              episode leaf
ani-ja-2013-attackontitan              anime season container
ani-ja-2015-kurokosbasketball-s0324    anime leaf (episode GLUED to the slug, not -eNN)
oth-football-2026-fifaworldcup-s01e01  sports
```

Category prefixes map to libraries: `mov`→movies, `tv`→series, `ani`→anime, everything else→other.

### 🔑 THE RULE THAT MATTERS: use the SHOW's first-air year for EVERY season

This is the single highest-value convention, and getting it wrong is expensive to undo.

| Convention | Enrich units | Where artwork lands |
|---|---|---|
| Per-season year (`…-1993-xfiles-s01`, `…-1994-xfiles-s02`) | **one per season** | inside each *season* folder |
| **Show year everywhere** (`…-1994-friends-s01`, `…-1994-friends-s02`) | **ONE for the show** | the **show** folder ✅ |

Why: unit grouping strips the trailing `-sNN` from the id. Identical years ⇒ identical derived key
⇒ one unit. And once a unit has **≥2 season folders**, `_show_folder_of` takes their `commonpath`
and resolves to the parent show folder — the proper Plex/Jellyfin layout.

**One command enriches the whole show** instead of one per season.

Real example of both, in this library:
- ❌ X-Files: 9 seasons, 9 units, 9 commands, artwork scattered into season folders.
- ✅ Friends: all seasons `tv-en-1994-friends-sNN`, one unit, one command, artwork at show level.

### Can folder names still use the real per-season year?

**Yes.** The two are completely independent, and this was verified by simulation:

- **Unit grouping reads the ID** — folder names play no part.
- **Artwork placement reads the PATHS** — `commonpath` compares directory *structure*, not the years
  printed in names.

So this is correct and recommended:
```
IDs      tv-en-1994-friends-s02      ← show year
Folders  Friends Season 02 (1995) [tmdbid-1668]   ← real air year, purely cosmetic to the code
```

### Folder layout

```
Friends (1994) [tmdbid-1668]\
  Friends Season 01 (1994) [tmdbid-1668]\
  Friends Season 02 (1995) [tmdbid-1668]\
```

- Season numbering starts at **01** (00 is the Specials convention; MediaVault uses `--extras` instead).
- The token on season folders is redundant but harmless (since IMP-U6 the season-inheritance
  artwork walk accepts a provider token on ANY ancestor shape — `[tmdbid-…]`, `[tvdbid-…]`,
  or the legacy curly form).
- **A malformed token breaks detection** — `tmdbid-1668]` (missing `[`) won't match, and enrich will
  append a second token.
- Case no longer matters (`{TMDB-…}` legacy and `[TmDbId-…]` square both match) as of **IMP-C23** +
  **IMP-U6**.
- **Legacy curly folders (`{tmdb-1668}`) are still recognized but never re-stamped.** Convert them
  once with `python tools/migrate_token_brackets.py` (dry-run first, then `--apply` — it renames via
  the journal-backed `rename_folder`, strictly sequentially, and backfills the NFO sidecar).

### One-time Plex setup so the token pins there too (IMP-U6)

Jellyfin and Emby read `[tmdbid-…]` straight from the folder name. **Plex** ignores square-bracket
tags as match hints — it pins the id from the `movie.nfo` / `tvshow.nfo` sidecar that enrich now
writes into every stamped folder (IMP-U6, D6). To switch Plex onto it (Plex Media Server **1.43+**):

1. Settings → **Metadata Agents** → add an agent that uses the **Plex NFO agent** (optionally
   stacked on the Plex Movie agent) for your movie/TV libraries — see
   [support.plex.tv/articles/using-nfo-metadata-files-with-plex](https://support.plex.tv/articles/using-nfo-metadata-files-with-plex/).
2. Re-scan the library after running the token migration; spot-check a few titles.

Until that switch, Plex simply matches by name+year (reliable; the id tag was always optional there).

### There is no "series map" entry

`ENTRY_TYPE_KEYS` has exactly three types: `leaf`, `season_map`, `multi_ep_alias`. The show level is
a **derived key** computed by stripping `-sNN` — never stored. You only ever create season ids and
leaf ids.

---

## 3. Disk, splitting and `rehash`

### `rehash` costs 2× the file. Budget for it.

| Mode | Extra disk needed | What it does |
|---|---|---|
| default (deferred) | **1×** file + buffer | canonical hash is blessed on first restore |
| `rehash` (eager) | **2×** file + buffer | merges the chunks back, hashes now, stores it for later verification |

Buffer is `max(1% of need, 2 GB)`.

**Worked example — a 75.6 GB remux:**
- no rehash → needs **77.6 GB**
- with `rehash` → needs **153.2 GB**

For 70 GB+ files, deferred is usually the right call regardless of free space — eager means merging
and hashing ~75 GB of chunks to front-load work the first restore does anyway.

The free-space check runs **before** any work and refuses cleanly if it won't fit, naming the number.

### `tempdir` — what it redirects

`tempdir <path>` redirects **both** the chunks **and** the eager rehash merge temp to another volume
(`main.py:4745`), and the free-space check targets **that** volume, not the source (`main.py:4835`).
The per-entry `temp_dir/<safe-id>` directory is cleaned up afterwards.

```
python main.py push <id> SIZE_MB 8000 tempdir D:\test
```

The directory must already exist. Not needed if the source file already sits on the roomy drive.

### Split sizing

`SIZE_GB 8` / `SIZE_MB 9600` / `COUNT 4` all work. A 35.6 GB file at `SIZE_MB 9600` → 4 chunks.

---

## 4. CLI traps

### ⚠️ Single-dash vs double-dash is inconsistent

| Flag | `-single` | `--double` |
|---|---|---|
| `-tmdbid` / `-tvdbid` | ✅ | ✅ |
| `-extras` / `-extras-size` | ✅ | ✅ |
| **`--nfo`** | ❌ | ✅ |
| **`--yes` / `--no-rename` / `--no-web` / `--no-nfo`** | ❌ | ✅ |

**An unrecognised token is silently absorbed into the file path.** Typing `-nfo` produces:
```
❌ File not found: …ESub.mkv -nfo
```
which looks like a missing file, not a bad flag. Unregistered papercut — a warning when an unmatched
token starts with `-` would turn this into an obvious error.

### `--nfo` writes an NFO. It does not require one to exist.

It generates `movie.nfo` / `tvshow.nfo` from TMDB. Off by default. Any scene-release `.nfo` already
in the folder is a different filename (a MediaInfo dump) — no collision.

---

## 5. Resuming after a failure

### Push failed (no device, no disk) — do NOT re-run the whole autopilot

If prep succeeded and push failed, the entry sits at `status="local_ready"` with the hash already
stored. **Re-running `prep_push_rep_enrich` re-hashes the entire file** — `local_ready` is not in
`cmd_prep`'s skip list (`main.py:1056`). For a 75 GB file that is a very expensive no-op.

**Resume from the failed step instead:**
```
python main.py push <id> SIZE_MB 8000 tempdir D:\test
python main.py replace <id>
python main.py set_tmdb <id> <tmdb_id>          # the -tmdbid died with the aborted run
python main.py enrich_metadata <id> --apply --nfo
```

Tracked as **IMP-D23** (add `push_rep` / `push_rep_season`, or make prep detect already-prepped).

### The resume hint is misleading

The tool prints *"or simply re-run this same command"* without mentioning it re-hashes. Accurate but
costly for large files. Unregistered papercut.

---

## 6. 🔴 SAFETY: never run two mutating commands at once

**Until IMP-C24 is fixed, running two library-mutating commands in parallel WILL silently corrupt
your library.** This has already happened once — see
`docs/feature-library-concurrency/SESSION_HANDOFF.md` §2 for the full incident.

Why: `load_library()` merges all four JSONs into one dict; `save_library()` rewrites **all four**
from that dict. There is **no lock**. The second process saves from a snapshot taken before the
first one's change, silently erasing it. Blast radius is **all four libraries**, not just the one
you're working in.

| ❌ Mutating — one at a time | ✅ Read-only — safe in parallel |
|---|---|
| `prep`, `push`, `push_group` | `local_status` |
| `replace`, `replace_group` | `check` |
| `restore`, `restore_group` | `verify_library` |
| `set_tmdb`, `set_uploaded` | `scan_unprepped` |
| `rename_folder` | `enrich_metadata` (dry-run, no `--apply`) |
| `enrich_metadata --apply` | |
| all `prep_push_rep*` autopilots | |

**Note:** `prep_push_rep_season` already runs `replace` internally per episode. There is nothing to
gain by running `replace` alongside it — that is exactly what caused the incident.

### How to spot the damage

An entry with a **dummy on disk** but `status != "archived"` or `uploaded != True`. `verify_library`
reports these. The danger: `push_group`'s skip test is only `uploaded == True` — it never checks
`status` or what's on disk, so it will happily upload a 9 KB dummy over a real cloud copy.

---

## 7. Maintenance protocol for this file

**When the user asks a practical "how do I…" or "why did this happen" question in any Claude session
and gets a verified answer, add it here.**

1. Put it under the right section, or add a section.
2. **Verify before writing.** Every claim should be checked against the code or the live library —
   cite `main.py:NNNN` where it helps. Do not write from memory.
3. If the answer is "that's a bug", name the IMP code, or register one if it doesn't exist.
4. Bump **Last updated**.
5. Keep it operational. What commands exist → `README.md`. How it's built → `ARCHITECTURE.md`.
   What to do and what will bite you → here.
