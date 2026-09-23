# MediaVault folder structure & naming conventions

**Last updated: 2026-09-23** · Established by IMP-U6; format settled by decisions
[D11](feature-token-brackets/DECISIONS.md) (the evidence) and D12 (the final pick).

This is the reference for how media is laid out on disk. Every rule here was verified against
**real Plex, Emby and Jellyfin installs**, not vendor documentation — the docs disagree with each
other and the most widely repeated claim about Plex turned out to be false.

---

## 1. The provider token

```
{tmdb-<id>}
```

Curly braces, bare `tmdb` keyword, TMDB id. One form for movies, series and anime alike.

### Why this exact form

A 20-folder matrix was scanned by all three servers, using nonsense titles (`Zyrquat A (1999) …`)
and deliberately wrong years, so a correct match could only come from reading the token:

| Token | Plex | Emby | Jellyfin |
|---|---|---|---|
| `{tmdb-680}` | ✅ | ✅ | ✅ |
| `[tmdb-27205]` | ✅ | ✅ | ✅ |
| `[tmdbid-603]` | ❌ | ✅ | ✅ |
| `{tmdbid-550}` | ❌ | ✅ | ✅ |
| `{tmdb=13}` · `[tmdb=238]` · `[tmdbid=11]` | ❌ | ✅ | ✅ |
| `(tmdb-105)` | ✅ | ❌ | ✅ |
| `{tmdb-12} [tmdbid-12]` (dual) | ✅ | ✅ | **folder disappears** |

**The rule that falls out: Plex rejects the `id` SUFFIX (`tmdbid`, `tvdbid`) and the `=` separator,
and is indifferent to bracket style.** The widely-repeated claim that Plex ignores square brackets is
false — `[tmdb-27205]` matched fine. The suffix was always the defect.

Both `{tmdb-…}` and `[tmdb-…]` work everywhere. Curly was chosen for one consistent form and because
it matches Plex's own published naming docs.

### Things that look reasonable and are not

- **Never `[tmdbid-…]` or `[tvdbid-…]`.** Invisible to Plex.
- **Never a dual token.** Jellyfin drops the folder from the library entirely — worse than either
  single form. It had been proposed as "the only way to satisfy all three"; that premise was wrong.
- **Never parentheses.** `(tmdb-105)` fails on Emby.
- **Never `=` as the separator.** Emby-only.
- **Never an id on a season folder.** See §3.
- **One token per folder.** A stale non-tmdb token is stripped when a folder is stamped.

### TMDB for every category

Series and anime use TMDB too, not TVDB. A 12-show matrix confirmed `{tmdb-…}` matches TV shows on
all three servers, and MediaVault refuses `-tvdbid` outright (IMP-D22, a different id space) — so one
id space serves the whole library.

---

## 2. Directory layout

```
C:\Media\
├── Movies\   <Language>\ [<Genre>\] <Movie folder>\        <video file>
├── Series\   <Language>\ [<Genre>\] <Show folder>\ <Season folder>\  <episode files>
├── Anime\    <Genre>\               <Show folder>\ <Season folder>\  <episode files>
└── Sports\   <Sport>\               <Event folder>\        <video files>
```

The genre level is **optional and inconsistent by design** — `Series\English\Classic\<Show>` is three
levels deep, `Series\Tamil\<Show>` is two. Nothing may depend on a fixed depth.

> ⚠️ **Language and genre folders are never given a token.** `Series\Tamil` and
> `Series\English\Classic` are organisational, not titles. Tooling identifies them structurally: a
> direct child of a category root (`Movies`, `Series`, `Anime`, `Sports`) is a language folder and can
> never be one show's folder. This rule exists because `Series\Tamil` held exactly one show whose
> folder name contained `S01`, so every content-based heuristic passed it and a migration proposed
> renaming the user's language folder.

---

## 3. Naming by folder kind

### Movie folder
```
<any name> {tmdb-<id>}

Oceans.Eleven.2001.DV.HDR.2160p.WEB.h265-EDITH {tmdb-161}
Black Friday (2004) {tmdb-28740}
```
The rest of the name is free — release/quality/source info is deliberately preserved. Only the token
is standardised.

### Show folder (parent of seasons)
```
<any name> {tmdb-<id>}

Peaky Blinders {tmdb-60574}
Silicon Valley (2014) {tmdb-60573}
Dark (2017) {tmdb-70523}
```

### Season folder — **no id**
```
<Show Name> Season <NN> (<season air year>)

Silicon Valley Season 01 (2014)
The Wire Season 01 (2002)
Peaky Blinders Season 06 (2022)
```

Zero-padded season number. The year is the **season's** first air date from TMDB, not the show's —
Friends runs `Season 01 (1994)` through `Season 10 (2003)`. `Season 00` is specials.

The show name is repeated on purpose: each season folder is uploaded to the phone individually, so
the name alone has to say what it is, without its parent path.

> ⚠️ **A season folder must never carry a provider id.** Friends S01's own TMDB *season* id is
> `4573` — and `4573` as a *show* id is **"Late Night with Conan O'Brien"**. Season ids and show ids
> share one numeric namespace, and no media server reads a season-level token, so writing one can only
> mislead a scanner. The show folder above it carries the identity.

### Flat show (one folder is both show and season)
```
<any name> {tmdb-<id>}

Chernobyl (Miniseries) 2019 2160p.DTS-HD.MA.5.1.DV {tmdb-87108}
Death Note (Complete Series) [1080p] (Dual Audio) {tmdb-13916}
```
Left structurally alone — it needs its token, because it *is* the show folder.

---

## 3a. NFO files — they OVERRIDE the folder token

Emby and Jellyfin read a sidecar `.nfo` in preference to the folder token. **A wrong NFO silently
defeats everything in §1.** Verified 2026-09-23: `Fringe (2008) {tmdb-1705}` — a correct folder —
displayed as *Barareh Nights* on both servers, because its `tvshow.nfo` carried
`<tmdbid>1701</tmdbid>`. The token was never consulted.

So the token is the *fallback*, not the authority, on two of the three servers.

### Placement — one per title, never per season

```
<Show folder>\tvshow.nfo      ✅ the only correct place for a show
<Movie folder>\movie.nfo      ✅
<Season folder>\tvshow.nfo    ❌ tells the scanner the SEASON is a show
```

A `tvshow.nfo` inside a season folder invites Emby/Jellyfin to treat that season as a separate
series. 60 such files existed on 2026-09-23 — artifacts of enriching *before*
`normalize_season_folders` moved tokens up to the show folder, back when `_show_folder_of`
legitimately resolved to the season folder because that was where the token lived. They were removed
and regenerated at show level.

### What a healthy NFO looks like

`<title>` must be the real title. A `<title>` like `mov-ta-2002-run` or `tv-en-1994-friends-s01e01`
is a **MediaVault library id that leaked into the file**, and both servers will display it verbatim.
37 NFOs were in that state on 2026-09-23. Regenerate with:

```powershell
python main.py enrich_metadata <id_or_prefix> --apply --nfo
```

> ⚠️ **Do not run that across `--library series` until IMP-U7 is fixed.** `_show_folder_of` cannot
> read the season-folder names `normalize_season_folders` creates, so the same run re-stamps provider
> tokens onto 47 season folders and undoes §3. Movies are unaffected.

---

## 4. Episode file naming

```
<Show>.S<NN>E<NN>.<release info>.mkv

Silicon.Valley.S01E01.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-NOGRP.mkv
```

`SxxExx` is what every server parses. Absolute numbering (`Show - 01.mkv`) matched correctly on all
three in testing, but only over a two-episode fixture — it was **not** tested across a season boundary,
which is the classic anime failure mode (episode 26 of a continuous run mapping to S02E01). **Prefer
`SxxExx` for anime.**

Filenames are never rewritten by any MediaVault command. This matters operationally: **fetch/restore
locates archived content by Google Photos search on `search_term`, which is built from the filename.**
Folder renames therefore cannot break fetching — but a filename rename would.

---

## 5. Keeping it that way

Two commands maintain the convention. Both are **dry-run by default**, ancestor-aware, deepest-first,
idempotent by re-detection with no state file, and drive the crash-safe `cmd_rename_folder` so
`folder_path` is rewritten for every affected library entry in one journalled transaction.

```powershell
python main.py migrate_provider_tokens              # dry run — read-only, safe anytime
python main.py migrate_provider_tokens --apply      # converts any old token to {tmdb-<id>}

python main.py normalize_season_folders             # dry run
python main.py normalize_season_folders --apply     # show folder gets the id; seasons get clean names
```

`--apply` writes a JSON report to `C:\Media\migration_reports\`. A stale `folder_path` whose directory
is gone appears in that report's `errors` array — surfaced rather than silently skipped. That is
correct behaviour, not a failure.

**Always run the dry run and read the output first.** Three real defects in these commands were caught
that way and by nothing else — a full fixture suite passed throughout. One of them proposed renaming a
language folder.

### What they will not touch

Folders no library entry references are left alone by design — the commands only rename what the
library knows about. As of 2026-09-22 that is 18 empty placeholder folders (future releases) still
carrying `[tmdbid-…]`. Harmless while empty; if media is ever added to one, rename it by hand or prep
it into the library first.

---

## 6. Detection is deliberately wider than emission

MediaVault **writes** only `{tmdb-<id>}`, but **recognises** `{tmdb-…}`, `[tmdb-…]`, `[tmdbid-…]` and
`[tmdbid=…]` in any casing. That asymmetry is intentional: a folder acquired from anywhere is still
understood, and the idempotency guard never double-stamps one. Do not narrow it.

One shared implementation in `mvcommon.py` (`has_tmdb_token`, `find_provider_tokens`,
`CANONICAL_TMDB_TOKEN_FMT`) serves both the stamping guard and the artwork ancestor-walk. This
vocabulary had previously drifted three times (IMP-C18, C22, C23) because a second copy existed and
diverged. There is now exactly one parser and every predicate derives from it.

> ⚠️ **The mkvmerge brace escape is load-bearing on the happy path.** `{`/`}` are libfmt
> replacement-field syntax, and the canonical token contains braces — so every split of an archived
> title depends on `split_video_file` doubling them for its `--split -o` argument (`main.py:409`). A
> `{tmdb-79660}` folder once aborted an entire prep→push→replace with `fmt::format_error`.
> `tests/test_split_brace_escape.py` pins it and must not be deleted as "no longer relevant".
