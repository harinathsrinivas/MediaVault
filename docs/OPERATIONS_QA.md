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

**Last updated:** 2026-10-02

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

### What token format does MediaVault write now, and why did it change twice?

**The canonical format is `{tmdb-<id>}`** — curly braces, the bare `tmdb` keyword, the same shape
for movies, series and anime (`mvcommon.CANONICAL_TMDB_TOKEN_FMT`, `mvcommon.py:712`).

It changed twice during IMP-U6 because the first two answers came from vendor docs and community
lore, and **testing against your actual servers contradicted both**. A 20-folder matrix was scanned
by real Plex, Emby and Jellyfin installs, with nonsense titles and deliberately wrong years so that
nothing but a token could produce a correct match:

| Token form | Plex | Emby | Jellyfin |
|---|---|---|---|
| `{tmdb-680}` | ✅ | ✅ | ✅ |
| `[tmdb-27205]` | ✅ | ✅ | ✅ |
| `[tmdbid-603]` | ❌ | ✅ | ✅ |

What that actually establishes:

- **Plex rejects the `id` suffix** (`tmdbid`, `tvdbid`) and the `=` separator. That is the half that
  matters.
- **Plex does not care about bracket style** — `{tmdb-…}`, `[tmdb-…]` and `(tmdb-…)` all work on
  Plex. The widely-repeated claim that Plex ignores square brackets is **false**, and believing it
  is what produced the earlier `[tmdbid-…]` answer.
- **Don't reach for parentheses or two tokens.** `(tmdb-105)` passed Plex and Jellyfin but failed on
  **Emby**, and a folder carrying two tokens (`{tmdb-12} [tmdbid-12]`) **disappeared from Jellyfin
  entirely**. Curly and square `tmdb` are the only forms that work on all three; one token, always.
- A parallel 12-show series/anime matrix confirmed the identical rule for TV, so TMDB covers every
  category and there is nothing to gain from a second provider (MediaVault refuses `-tvdbid`,
  IMP-D22).

**Detection is deliberately wider than emission.** `mvcommon.find_provider_tokens` still recognizes
`{tmdb-…}`, `[tmdb-…]`, `[tmdbid-…]` and `[tmdbid=…]`, case-insensitively, so a folder that already
carries any of them is never given a second token — you will only ever see ONE tmdb token per
folder. That superset is not politeness: your library is still full of the old spellings until you
run the migration below, and every idempotency guard and the artwork-inheritance walk depend on
recognizing them in the meantime.

Nothing breaks if you do nothing — the new format only applies going forward, to folders
`enrich_metadata` newly stamps. To convert what you already have, see the next entry.

### How do I bring my existing library over? (`migrate_provider_tokens` + `normalize_season_folders`)

Two commands, and they do different jobs. **Both are dry-run by default. Always read the preview
before you pass `--apply`** — these rename real folders.

```
python main.py migrate_provider_tokens              # 1a. preview
python main.py migrate_provider_tokens --apply      # 1b. convert token SPELLING

python main.py normalize_season_folders             # 2a. preview
python main.py normalize_season_folders --apply     # 2b. restructure show/season NAMES
```

**`migrate_provider_tokens`** fixes the *spelling* of tokens already on disk — every
`[tmdbid-…]` / `[tmdb-…]` / `[tmdbid=…]` / wrong-cased folder becomes `{tmdb-<id>}`. It is
**ancestor-aware**: it also catches a show's top-level folder even when every library entry's
`folder_path` only points at a season underneath it, so it is the right tool even if an earlier
ad-hoc rename already got most of your leaf folders. Renames run deepest-first, and re-running is
free — an already-canonical folder is simply no longer a candidate. `--library
movies|series|anime|others` narrows the scope; a **mistyped** value is refused outright rather than
silently widening to your whole library. Real dry run on this library:
`scanned=317 would-rename=236 already-canonical=1`.

**`normalize_season_folders`** fixes *which folder carries a token at all* — the structural job.
Phase A gives the **show** folder its id (`Peaky Blinders` → `Peaky Blinders {tmdb-60574}`),
stripping any stale non-tmdb token so exactly one remains (`Dark (2017) [tvdbid-334824]` →
`Dark (2017) {tmdb-70523}`). Phase B renames each **season** folder to
`<Show Name> Season <NN> (<season air year>)` with **no id**, using one TMDB call per show. It
refuses to touch a category or language folder, leaves flat shows alone, and never invents a year —
a season TMDB has no air date for is skipped, not guessed. Real dry run on this library:
`scanned=64 show_folders_would_token=11 seasons_would_rename=60 skipped=4`.

**Run them in that order**, for a concrete reason: Phase A only stamps a show folder that has **no**
tmdb token at all, and detection recognizes the old spellings — so a show folder already carrying
`[tmdbid-…]` is left alone by `normalize_season_folders` and keeps the old spelling until
`migrate_provider_tokens` converts it. Running the spelling pass first means the structural pass
finds everything already canonical. (Neither command is destructive in the other order; you would
just need a second `migrate_provider_tokens` run afterwards.)

Neither one is "just cosmetic" — both rename real folders, including already-pushed content
(`migrate_provider_tokens` flags those as `remote-bearing` in its preview). Preview both.

### `--apply` wrote a report with a non-empty `errors` array. Did the run fail?

**No.** `--apply` always writes a JSON report to `<LOCAL_ROOT>\migration_reports\`
(`token_format_<UTC timestamp>.json` / `season_folders_<UTC timestamp>.json`; a same-second re-run
gets a `-1` suffix instead of clobbering the earlier one). A non-empty `errors` array is the command
**telling you about folders it could not rename, having continued with everything else** — that is
the designed behaviour, not an abort.

The common entry is a **stale `folder_path`**: the library points at a directory that is no longer
on disk (you moved or deleted it outside MediaVault). Neither command pre-filters those. The rename
is attempted, `rename_folder` refuses it with `❌ No such folder (or unknown id)` (`main.py:4051`),
and the refusal is recorded rather than silently skipped — **surfacing it is the point**, because a
stale pointer is a real library problem you want to know about. Fix it with `rename_folder` or by
re-prepping, then re-run the migration; it will pick the folder up on the next pass.

Two other things land in `errors`:

- A rename that crossed its **point of no return** (folder moved, library rewrite failed). The record
  carries a `resume_cmd` — run exactly that, then re-run the migration.
- For `normalize_season_folders`, a Phase A failure aborts **that show's** Phase B (the parent may be
  half-renamed) and lists the untouched seasons on the record's `seasons_not_attempted`. Other shows
  are unaffected.

Not everything lands in `errors`. A group the **category-folder guard** declined — a language folder,
a genre folder holding several shows, a season whose show has no discoverable TMDB id — goes to
`skipped` with a plain-English reason, as does a season whose parent folder is missing entirely.
Read `skipped` when a show you expected to be renamed wasn't.

As a byproduct, IMP-U6 also fixed a **live regression**: season/episode artwork inheritance walks
UP the directory tree looking for the nearest tmdb-tokened show folder, and that walk's predicate
had drifted to curly-brace-only while an earlier external migration had already renamed most of the
real library to `[tmdbid-…]` — so the walk matched almost nothing and inherited posters/fanart were
silently missing library-wide. It now shares the same detection helper
(`mvcommon.has_tmdb_token`) the stamping idempotency guard uses, so the two cannot drift apart
again. (The fix was to share the **superset** detector, not to pick a bracket — which is why later
settling on curly `{tmdb-…}` for emission did not bring the bug back.)

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
Folders  Friends Season 02 (1995)    ← real air year, purely cosmetic to the code
```

### Folder layout

```
Friends (1994) {tmdb-1668}\
  Friends Season 01 (1994)\
  Friends Season 02 (1995)\
```

- **The id goes on the SHOW folder. A season folder must never carry one.** Not a style preference:
  season ids and show ids share one numeric TMDB namespace, so a season token is an id that means
  something else. Friends S01's own TMDB *season* id is `4573` — and `4573` as a *show* id is "Late
  Night with Conan O'Brien". No media server reads a season-level token anyway, so writing one can
  only mislead a scanner. `normalize_season_folders` strips them (see §1).
- The season folder earns its keep by being **self-identifying by name** — `<Show Name> Season <NN>
  (<air year>)` — because each season is uploaded to a phone on its own.
- Season numbering starts at **01** (00 is the Specials convention; MediaVault uses `--extras` instead).
- **A malformed token breaks detection** — `tmdb-1668}` (missing `{`) won't match, and enrich will
  append a second token.
- Case no longer matters (`{TMDB-…}` works) as of **IMP-C23**.

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

### A push deleted my master and uploaded it without its `[short_id]` tag (a `_parts` folder name) — IMP-C28, fixed

**Symptom.** You pushed a title as a whole file, and its folder or file name contains `_parts` in lower
case (`Spare_parts (2015)`, `Body_parts`). The phone received `Spare_parts (2015).mkv` instead of
`Spare_parts (2015) [<short_id>].mkv`, and the local master was gone. The push still ended
`✅ SUCCESS`, right after a line like:
```
⚠️  INTEGRITY: <id> status=onboarded but on-disk=MISSING — run 'python main.py verify_library'
```

**Why.** Before IMP-C28, `cmd_push` decided "chunk or whole file?" by whether the file's *path*
contained `_parts` (the chunk-dir name) anywhere. A master in such a folder counted as a chunk, so it
kept its bare name, and after the upload it was deleted the way an uploaded chunk is.

Only pushes that upload the master whole were affected:
- a plain `push`;
- a split skipped because the file is under the target size;
- a `tempdir` push that did not split.

Split pushes and `--extras` were never affected, because extras decide by whether that item was split
(`main.py:6096`, `main.py:6137`). The test was case-sensitive, so `Spare_Parts` and `Spare Parts`
were never hit.

**Fixed:** a file is a chunk only if it sits directly in that push's own chunk dir
(`mvcommon.in_parts_dir`, `main.py:6643`). The identity capture uses the same rule.

**Is anything lost? No bytes.**
- The delete ran only after the upload and its rename had succeeded (`main.py:6696-6717`), so the
  cloud copy is the master's bytes, and the library `hash` still matches them.
- Fetch searches a whole file by its plain local filename (`mainfetch.py:331`), which is exactly the
  name that upload has, and it matches downloads by hash (`mainfetch.py:400-404`). So
  `fetch_restore <id>` is the way to get the master back.
- What stays wrong is the name in the cloud. The `.mvmeta.json` sidecar (`main.py:5697`), the
  `search_term` and the identity capture all record the tagged name, so name-based matching
  (IMP-C25) cannot tie that item to its entry.

**⚠️ If `replace` ran afterwards** (for example the `prep_push_rep` autopilot's next leg), it found no
master. It skips its rename when the original is absent (`main.py:7154`), writes the dummy in its
place and marks the entry `archived` without complaint. That entry now looks healthy.

**Find the entries that may have been hit.** Both checks are read-only; run them from the repo root:
```
python main.py verify_library
python -c "import os, mvcommon as m; [print(k, e.get('status')) for k, e in m.load_library().items() if e.get('filename') and e.get('uploaded') and '_parts' in os.path.join(e.get('folder_path') or '', e['filename']) and not (e.get('split_info') or {}).get('is_split')]"
```
- `verify_library` catches the push-only case: it reports
  `status=onboarded  on-disk=MISSING (missing)  [onboarded_missing]`.
- The one-liner lists every uploaded whole-file entry whose path contains `_parts`, including ones
  `replace` has already archived. Check each one's name on the phone or in Google Photos.

### `scan_unprepped` and the reclaim view skipped a real video named like `the.chunk.2019…` — IMP-C30, fixed

**Symptom.** A real, unprepped video whose lower-case file name contains `.chunk.` was missing from
`scan_unprepped` and from the web Disk Reclaim view and folder tree. An example is
`the.chunk.2019.1080p.web.h264.mkv`. `scan_unprepped` could even end with
`✅ All libraries are completely in sync.` For such a title, `push <id> chunks 1-2` refused with
`No chunks found in range`.

**Why.** Before IMP-C30, both scans skipped any file whose name merely *contained* `.chunk.`, meaning
to hide leftover chunks. The `chunks N-M` filter read a chunk's number from the *first*
`.chunk.<digits>.` in its name, which for that title is the year. The test was case-sensitive, so
`The.Chunk.…` names were never hit.

**Fixed:** a file is a chunk only if its name ends in `.chunk.<digits>.mkv`, the exact form the split
writes (`mvcommon.chunk_index`, used at `main.py:8980`, `main.py:10306` and `main.py:6576`). Real
chunks are still never listed, wherever they lie.

**Is anything lost? No.** Both scans are read-only, and a range push never marks an entry onboarded.
The file was simply not listed: run `python main.py scan_unprepped` again to see it.

---

## 4. CLI traps

### ⚠️ Single-dash vs double-dash is inconsistent

| Flag | `-single` | `--double` |
|---|---|---|
| `-tmdbid` / `-tvdbid` | ✅ | ✅ |
| `-extras` / `-extras-size` | ✅ | ✅ |
| **`--nfo`** | ❌ | ✅ |
| **`--yes` / `--no-rename` / `--no-web`** | ❌ | ✅ |

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
`cmd_prep`'s skip list (`main.py:1389`). For a 75 GB file that is a very expensive no-op.

If the push had already split the file and stopped part-way, the re-run is worse than slow: the
re-prep drops the entry's split record, and since IMP-C32 the push then **refuses** the leftover
chunks (see "Push refuses" below). `push <id>` resumes them properly.

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

### Push refuses: `Cannot resume <id>: …\_parts holds no chunk recorded in its split` — IMP-C32 (and IMP-C31)

**The rule.** A push resumes from `_parts/` whenever that folder is not empty. It uploads only the
files this entry's own `split_info` records (its chunks and its FLAC holder), after checking each
one's bytes against the recorded SHA-256 (`main.py:6318-6363`, `main.py:6592-6608`). Everything else
in the folder is left where it is and listed with its likely owner. If nothing recorded is left to
upload, the push refuses. **Nothing was uploaded, the library entry is unchanged, and the master is
intact.** Extras follow the same rule in their own `<extra folder>\_parts\<short_id>`
(`main.py:5989-6018`).

**Why.** `_parts/` belongs to a folder, not to an entry, and every episode of a season is pushed
through the same `Season NN/_parts/` (an episode's `folder_path` is its season folder,
`main.py:1416`). Before IMP-C32 a resume uploaded every `.mkv` it found there and marked the entry
`onboarded`. That could be another episode's waiting chunks, or chunks that no split records.
`replace` then swapped the master for a dummy, because it checks only `uploaded` (`main.py:7108`).
Before IMP-C31 even a folder holding no chunk at all "resumed" and printed `✅ SUCCESS`.

**What to do: read the list the refusal prints.** Each line names a file and says whose it is.

| The note says | What it is | What to do |
|---|---|---|
| `a recorded chunk of <other id>` | another episode's interrupted push | `python main.py push <other id>` first, then push this one again |
| `tagged for this entry, which has no recorded split` | this entry's own chunks, but its split record is gone (see the ⚠️ below) | delete them, then push again with a split size |
| `…, but not in its recorded split`, `…no library entry has that id`, `no entry tag` | leftovers: a failed split's partial chunk or lone FLAC holder, an old split, a stray file | delete them, then push again with a split size |

- Push again **with a split size**, e.g. `python main.py push <id> SIZE_GB 8`; the refusal suggests
  one when it knows it. A plain `push <id>` uploads the file whole, and if the entry still records
  the old split it keeps that stale record (open problem, unregistered).
- Only if every chunk already reached the device, for example because you finished the title with
  `chunks N-M` range pushes (which never mark an entry uploaded): check that on the phone or in
  Google Photos first, then run `python main.py set_uploaded <id>`.
- When the folder also holds chunks this entry does record, the push resumes those and only lists
  the rest (`⚠️ Not part of this entry's recorded split — left in place, not uploaded`).

**`… chunk(s) … no longer match the hash its split recorded`.** A recorded chunk's bytes changed
since the split: a partial write, or a file that was edited or replaced. It is never uploaded.
Delete this entry's chunks in that folder (the files carrying its `[<short_id>]`), then push again
with a split size.

**⚠️ After an interrupted split push, resume with `push <id>`, before anything re-preps the entry.**
`push <id>` resumes from the recorded split. But `prep` rebuilds a `local_ready` entry from scratch
(`main.py:1495-1512`), and that drops its split record. Re-running `prep_push_rep`, or the
`Resume the rest of the season: prep_push_rep_season …` line the season autopilot prints, re-preps
the interrupted episode first. The push then refuses its leftover chunks as
`tagged for this entry, which has no recorded split`.
- Right order: `python main.py push <id>` (it prints `Resuming N chunks`), then the season command
  for the rest. The season command leaves the pushed episode's prep and push alone and goes on to
  `replace`.
- If the re-prep already happened: delete that episode's leftover chunks from `_parts/` and re-run.
  The episode is split and uploaded again in full. Chunks the interrupted run had already uploaded
  stay in Google Photos under the same names, so delete those older copies there. A fetch accepts a
  download only when its hash matches (`mainfetch.py:404`), so a stale copy can make a fetch fail but
  can never restore the wrong bytes.
- Whether `prep` should keep the split record of an unchanged file is an open decision (see
  `improvements_tierC.md` IMP-C32).

**Entries the old behaviour already archived without a split record.** Before IMP-C32 that re-run
uploaded the leftover chunks and archived the episode with no `split_info`. Its chunks are in the
cloud, but `fetch_restore` looks for one whole file (`mainfetch.py:286-335`) and cannot find it.
This read-only check lists uploaded entries that have chunk sidecars in `checksums/` but no recorded
split (run from the repo root):
```
python -c "import os, mvcommon as m; [print(k, e.get('status')) for k, e in m.load_library().items() if e.get('filename') and e.get('uploaded') and not (e.get('split_info') or {}).get('is_split') and os.path.isdir(os.path.join(e['folder_path'], 'checksums')) and any('[%s].chunk.' % e.get('short_id') in n for n in os.listdir(os.path.join(e['folder_path'], 'checksums')))]"
```
- On 2026-10-02 it listed three: `tv-en-2003-thewire-s02e12`, `tv-en-2017-dark-s01e10` and
  `tv-en-2004-battlestargalactica-s01e11`. The Google Photos inventory shows both chunks of each in
  the TV account, so no bytes are lost.
- The fix for such an entry is to rebuild its `split_info` from the `checksums/<chunk>.sha256`
  sidecars, as the legacy reconcile did for 27 entries (`docs/feature-legacy-reconcile/REPORT.md`).
  No command does this yet (unregistered). A failed split followed by a whole-file push leaves the
  same sidecars behind, so check the cloud copy before rebuilding.

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
| `enrich_metadata --apply` | `migrate_provider_tokens` / `normalize_season_folders` (dry-run, no `--apply`) |
| `migrate_provider_tokens --apply` | |
| `normalize_season_folders --apply` | |
| all `prep_push_rep*` autopilots | |

**Note:** `prep_push_rep_season` already runs `replace` internally per episode. There is nothing to
gain by running `replace` alongside it — that is exactly what caused the incident.

### How to spot the damage

An entry with a **dummy on disk** but `status != "archived"` or `uploaded != True`. `verify_library`
reports these. The danger: `push_group`'s skip test is only `uploaded == True` — it never checks
`status` or what's on disk, so it will happily upload a 9 KB dummy over a real cloud copy.

---

## 6a. Media-server library setup (Plex / Emby / Jellyfin)

Verified against the live servers on 2026-09-22/23.

### Plex hides shows nested more than 3 levels below the library root

`TV Shows` showed **2 shows instead of 15**, and a forced full rescan changed nothing. Counting
episode depth below the library root explained it exactly:

| episode file depth below library root | scanned? |
|---|---|
| 2 (`<Show>/ep.mkv`) | yes |
| 3 (`<Genre>/<Show>/ep.mkv`) | yes |
| **4** (`<Genre>/<Show>/<Season>/ep.mkv`) | **no — 891 files invisible** |

MediaVault's layout is `Series/<Language>/<Genre>/<Show>/<Season>/ep`, which puts episodes 4 deep.
**Fix: point the library at the genre folders, not at `Series`** — one library, several locations, so
each location root sits directly above the show folders. 2 shows → 15, 908 episodes. This is a Plex
database change only; nothing on disk moves.

### Overlapping library paths silently duplicate items

A library whose path *contains* another library's path makes both index the same files. Found in
Plex (`Movies SSD` = `C:\Media\Movies` over six per-language libraries) and in Emby
(`Movies SSD`, `TV shows SSD`, and `English Movies` over `3D Movies`).

**Before deleting the broad library, check what only it covers.** Deleting Plex's `Movies SSD` would
have orphaned `Movies\Korean` (3 films, no Korean library existed); deleting Emby's would have
orphaned `Movies\Kannada`, and `TV shows SSD` was the only cover for `Series\Tamil`. Create the
missing narrow libraries first, then delete.

Watch state survives: Plex's `Movies SSD` held 14 watched / 29 in progress, and the six language
libraries held exactly the same, because Plex syncs state across duplicate items.

To edit a Plex library's paths **losslessly** (instead of delete + recreate, which loses watch state)
send the FULL parameter set — `location` alone returns 400:

```
PUT /library/sections/{id}?name=…&type=…&agent=…&scanner=…&language=…&location=<path>
```

### Emby: delete a library by `Id`, not by `name`

`DELETE /Library/VirtualFolders?name=…` returns **500 `Object reference not set to an instance of an
object`** for every variant — with or without `refreshLibrary`, and the `Paths` form fails the same
way. Using the numeric id works:

```
DELETE /emby/Library/VirtualFolders?Id=<ItemId>&api_key=…      ->  204
```

Get `<ItemId>` from `GET /emby/Library/VirtualFolders`. (An earlier note in this project claimed Emby
libraries could only be removed through the UI — that was wrong; only the `name=` form is broken.)

### Auth differs per server

| Server | How the key goes |
|---|---|
| Plex | header `X-Plex-Token` (query param 404s on some endpoints) |
| Emby | query param `api_key=` |
| Jellyfin | header `Authorization: MediaBrowser Token="…"` (query param → 401) |

Plex's token is in the registry: `HKCU:\Software\Plex, Inc.\Plex Media Server` → `PlexOnlineToken`.

### Auditing whether every item is matched correctly

Don't compare item counts between servers — each has a different library set. Join on **each item's
own path** and read the token out of it, then compare against what the server matched
(`Guid`/`ProviderIds`). Across 214 tokened folders on 2026-09-23 this found 0 unmatched on all three
and 4 genuinely wrong ids.

One caveat that matters: "server agrees with our token" is **not** "the item is correct". If the
token itself is wrong and every server obeys it, an id comparison calls it correct. Resolve each
token against TMDB and compare the real title to the folder name — that is what caught
`Ant-Man and the Wasp {tmdb-227914}` pointing at *Chainsaw Scumfuck*. See IMP-U8.

---

## 6b. Fetch — which Google account

### An episode lives in another Google account — how do I fetch it?

Fetch picks the Chrome profile, and so the Google account, from the **id prefix**
(`mainfetch.profile_for_id`, `mainfetch.py:730`): `mov-` → movies, `tv-` → tv, `ani-` → anime,
`oth-` → others. An item that was backed up to a *different* account is never found that way — the
search runs in the wrong account. A single file ends `❌ ENTRY INCOMPLETE` after two ~5-minute waits
(`mainfetch.py:362`); in a batch, the third empty search in a row aborts the run with a misleading
*"Profile … is logged out"* message (`mainfetch.py:228`). The 2026-09-25 inventory mapping found
**31 X-Files episodes (seasons 2–4) in the movies account** (IMP-C26).

**Fix — tell fetch where they live**, in your gitignored `mvconfig.json` (repo root):

```json
"fetch_account_overrides": {
  "tv-en-1994-xfiles-s02e03": "movies",
  "tv-en-1995-xfiles-s03": "movies"
}
```

- Keys are **exact manual ids or id prefixes**; values are `movies` / `tv` / `anime` / `others`.
  The **longest matching key wins**, so an exact id beats a prefix (`mainfetch.py:730`).
- Prefixes are plain string prefixes (`…-s02e1` also matches `…-s02e10`–`s02e19`). Use a season
  prefix **only if every episode of that season lives in that account** — otherwise list the exact
  ids. A season fetch with a prefix that is too broad would send the other episodes to the wrong
  account.
- An unknown account (e.g. `"series"`) prints **one** `⚠️  mvconfig.json: fetch_account_overrides
  entry …` warning and that entry is ignored; a blank key is refused the same way — it would match
  every id (`mainfetch.py:696`). Every fetch run re-reads `mvconfig.json` (`main.py fetch` spawns a
  fresh `mainfetch.py` process, `main.py:9519`), so an edit applies to the next fetch.
- Then fetch as usual: `python main.py fetch tv-en-1994-xfiles-s02e03 tempdir D:\MV_fetch` — the
  `[Account] Profile for …` line should now say `'movies'`.

**A season whose episodes span accounts is fetched in one run** — one Chrome session per account,
one after another, under the same fetch lock (`mainfetch.py:808`). The season's own account goes
first with the usual `[Account] Profile for …` line; each further account prints
`> [Account] Switching to profile '<account>' for N item(s) (fetch_account_overrides)`. Before the
switch, the previous account's Chrome windows are closed and debug port 9222 must be free; if it
stays busy the run stops with `❌ Cannot switch to profile …` — close that Chrome window and re-run.
Files already fetched stay in their `restore` folder and are skipped on the re-run. A logged-out
account is reported by *its own* profile name. `--fetchExtras` extras follow their **title's** id
(the season), not the episode you typed.

No overrides configured → fetch behaves exactly as before. IMP-C25 will learn each item's real
account automatically; this list stays the manual override on top of it.

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
