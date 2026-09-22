# IMP-U6 — Branch comparison: `feature/imp_u6_provider_tokens` (C, Claude) vs `feature/imp_u6_token_brackets` (D, DeepSeek)

**Judge:** V2 judge (Fable 5.1, xhigh effort). RUNNING AS: `claude-fable-5-1` — this review ran on
the primary model tier, not the Opus fallback.

**Status: nothing has been merged, checked out, or changed by this review.** Both branches were
inspected read-only via `git show` / `git diff` against `562fb4a` (the common ancestor) and one
disposable `git worktree` (created and removed for D's test run — never the main working tree).
The only write this review performed is this file. Two test suites were **executed** (read-only,
no `--apply` anywhere, no `library_*.json` touched) to verify the pass-count claims; a real,
**read-only** listing of `C:\Media` and a **read-only** diff of D's own committed pre-migration
backup against the live `library_series.json` were also done to verify the live-library claims.
Full list of what was and wasn't independently verified is in the Confidence section at the end.

---

## Verdict

**Ship C (`feature/imp_u6_provider_tokens`) as the base, then graft two things from D onto it
before merging: the NFO-at-stamp feature and the one-line `card.js` fix.** Confidence: high on the
code-quality/architecture comparison, high on every number quoted below (independently
re-executed or re-derived from the actual diffs), medium on "which artwork-walk design is
objectively correct" (a real, still-debatable tradeoff, argued below).

**One-line reason:** both branches are fully green and non-trivial engineering, but C's detection
is *verifiably more complete* on exactly the drift-bug class that has already bitten this project
three times (IMP-C18/C22/C23) — D's own core `_has_tmdb_token` fails to recognize two real,
in-the-wild token spellings (bare-keyword square `[tmdb-…]` and Emby's `[tmdbid=…]`) that C
explicitly handles and tests — while D's standalone migration tool's committed "audit trail"
turned out, on inspection, to be stale and internally inconsistent with both its own commit
message and the live filesystem. D's one clear, uncontested win — the NFO-at-stamp feature — is
high quality and worth taking; it is a clean graft onto C, not a reason to prefer D's branch as
the base.

---

## How the two branches relate to each other (important context, verified via git log + docs)

This was not two independent teams working in a vacuum. Timestamps (`git log --format=%ci`) show:

- D started at **02:51** on 2026‑09‑07, ran its live-library `--apply` migration at **~04:12**,
  and opened PR #51 the same session.
- C started at **18:09** the same day — **after** D's migration had already run against the real
  `C:\Media` and the real `library_*.json` files — and C's own planning docs say so explicitly:
  `docs/feature-token-brackets/DECISIONS.md` D2 references "the other tool's `03:52` backup" and
  "the other tool's already-verified-safe work"; `PLAN.md` names the exact same fact repeatedly
  (lines 38, 56, 137, 389, 420, 426, 466, 487, 645, 733, 768, 804, 812, 816).

Two consequences that change how several claims should be read:

1. **C's "did not touch the real library" is not a symmetric process choice — the library was
   already migrated by the time C ran.** C's D6 decision explicitly declines to *re-touch* work D
   had already done, rather than declining to do it at all. See "Where they genuinely conflict"
   below.
2. **D's live migration created a real, verified regression on unpatched `main`** that persists
   **today** (`main` tip `441e92f`, 2026‑09‑21, still has the brace-only
   `_PROVIDER_TOKEN_RE = re.compile(r"\{tmdb-[^}]+\}", ...)` at `main.py:10037`). Because D
   renamed ~196 real folders to `[tmdbid-…]` while `main`'s artwork-ancestor-walk and
   stamp-idempotency predicates were still curly-brace-only, **artwork inheritance for most of the
   real library has been silently broken on `main` for two weeks**, and any enrich run on
   unpatched `main` risks a double-stamp. C's own `docs/feature-token-brackets/PLAN.md:38` names
   this explicitly: *"This is a live regression on `main` today, caused by the other tool's
   [migration]."* Both branches' code changes happen to fix this (the fix is required for the
   feature itself, so it's not a differentiator between them) — but **neither branch has merged**,
   so the live regression is still open right now. This is the strongest practical argument for
   merging *something* soon, independent of which branch wins.

---

## Feature-coverage matrix

| Capability | C | D | Evidence |
|---|---|---|---|
| Canonical `[tmdbid-<id>]` emission | ✅ | ✅ | `mvcommon.py:702` / D `main.py:226` `format_tmdb_token` |
| Legacy `{tmdb-…}` / `{TMDB-…}` recognized forever | ✅ | ✅ | both |
| Bare-keyword square `[tmdb-…]` recognized by the **live enrich idempotency guard** | ✅ | ❌ (only in the standalone migration tool, not in `main.py`'s `_has_tmdb_token`) | verified live: `_TMDB_TOKEN_RE.search("[tmdb-12345]")` → `False` on D |
| Emby `=` variant `[tmdbid=…]` recognized anywhere | ✅ (`mvcommon.py` test `test_e_emby_equals_variant_found`) | ❌ (not in `main.py`, not in `tools/migrate_token_brackets.py`) | verified live: `False` on both of D's regexes |
| `[rartv]`-style release-group tag correctly rejected | ✅ tested (`test_provider_tokens.py::test_f_…`, `test_migrate_provider_tokens.py::test_rartv_…`) | presumably correct by construction (fixed provider-prefix alternation) but **untested anywhere** | grep confirms no `rartv` test in D |
| Mismatched-bracket rejection (`{tmdb-123]`) tested | ✅ | not explicitly tested | C `test_g_mismatched_brackets_not_found` |
| Shared, single-source-of-truth parser | ✅ `mvcommon.find_provider_tokens`, one regex pair, old `_PROVIDER_TOKEN_RE` **deleted** | ✅ one regex block in `main.py`, old copy **deleted** — but drift-pin tests compare the predicate to *its own* regex, not to independent expected outputs | both close the C18/C22/C23 drift class structurally; C's tests additionally pin exact expected outputs |
| Artwork-ancestor-walk: TMDB-only vs any-provider | TMDB-only (`mvcommon.has_tmdb_token`) | any-provider/any-shape (`_has_provider_token`) | genuine design difference — see below |
| NFO written at stamp time, default ON | ❌ (documented as an accepted tradeoff, not built) | ✅ `--no-nfo` opt-out, never-overwrite guard | D `main.py:2439` `_write_nfo`, tested end-to-end in `test_token_brackets.py` |
| Migration tool | in-CLI `migrate_provider_tokens` command (`cmd_migrate_provider_tokens`, `main.py:4165`) | standalone `tools/migrate_token_brackets.py` (405 lines) | both dry-run-default, both drive the existing crash-safe `cmd_rename_folder` |
| Migration tool covered by the smoke gate | ✅ (`tests/smoke/test_smoke_all_commands.py`) | ❌ (deliberately excluded — "keeps the CLI surface and smoke-gate contract stable") | numstat: C's smoke file +54/-6, D's is a pure fixture swap |
| Migration tool's own dry-run matches live reality today | ✅ — independently re-run, `scanned=317 would-rename=1 already-canonical=236`, matches exactly | not re-checked live (D's own tool wasn't re-run by this review; see below) | verified live by this review |
| Committed migration transcripts as an audit trail | N/A (JSON report at `--apply` runtime instead) | claimed feature, but the **committed** `MIGRATION_APPLY_2026-09-07.txt` is stale/inconsistent — see below | verified by this review |
| `suggest_target_folder` unifies placeholder on TMDB for series/anime too | ❌ (movies → `[tmdbid-…]`, series/anime still → `[tvdbid-…]`) | ✅ all categories → `[tmdbid-0000000]` | C `main.py:9083`; D `main.py:8393` |
| `webui/static/card.js` hint string matches the new bracket format | ❌ **bug, unfixed** — still literal `"{" + field + "-…}"` | ✅ fixed to `"[" + field + "id-…]"` | C never touches `card.js`; D `card.js:566` |
| Live library actually migrated | ❌ (already done by D; C's own dry-run confirms) | ✅ — but the checked-in transcript undercounts what actually happened (see below) | this review's independent PowerShell listing + Python backup diff |
| `ENTRY_TYPE_KEYS` untouched | ✅ | ✅ | `git diff` shows zero `+`/`-` lines inside the dict body on either branch |
| Rollback journal / PONR / `RollbackHardFail` definitions untouched | ✅ | ✅ | grep for the class/def names returns nothing on either diff |
| Net-new tests (function-def count, own contribution only) | ~32 | ~20 (7 removed are deliberate pin-flips with direct replacements, not coverage loss — verified) | see "Test quality" below |
| Full suite green | ✅ 938 passed, 0 failed (re-run) | ✅ 911 passed, 0 failed (re-run in a disposable worktree) | this review |
| Smoke green | ✅ 81 passed (re-run) | ✅ 80 passed (re-run) | this review |

---

## The two genuinely deep findings this review surfaced beyond the orchestrator's brief

### 1. D's core detection has a real double-stamp gap — worse than the Emby `=` miss alone

D's own migration-tool commit history records a mid-session discovery: *"the first dry-run exposed
a 4th legacy shape the classifier missed — the user's own MANUAL pre-IMP-U6 renames used square
brackets with the OLD keyword (`John Wick (2014) [tmdb-245891]`), which Jellyfin/Emby do NOT
parse. `_SQUARE_OLD_TMDB` added"* (`docs/feature-token-brackets/PROGRESS.md`, run-history entry).
D fixed this **only inside `tools/migrate_token_brackets.py`** (`_SQUARE_OLD_TMDB = re.compile(r"\[tmdb-([^\]]+)\]", ...)`,
tool-local). It was **never propagated to `main.py`'s own `_has_tmdb_token`/`_TMDB_TOKEN_RE`** —
the predicate the *live* `cmd_enrich_metadata` / `_enrich_after_archive` idempotency guard
actually uses. Verified by executing D's exact regex:

```
_TMDB_TOKEN_RE = re.compile(r'(?:\[tmdbid-[^\]]+\]|\{tmdb-[^\}]+\})', re.IGNORECASE)
'[tmdb-12345]'  -> False   # a real, user-created folder shape D's own tool found
'[tmdbid=12345]' -> False  # Emby's documented '=' variant
```

Both are real shapes: the John Wick case is 4 actual folders in the user's library that D's own
migration tool had to special-case; the Emby `=` case is the exact community-forum source
(`emby.media/community/topic/102371`) that *both* branches' planning docs cite. If a user (or a
future manual rename) leaves a folder as `Movie (2020) [tmdb-12345]`, D's `cmd_enrich_metadata
--apply` on that branch would not recognize it as already-tokened and would append a second token
— the exact IMP-C23 double-stamp class this whole feature exists partly to close. C's shared
`mvcommon.find_provider_tokens` recognizes both spellings by construction (`_PROVIDER_BY_TAG` maps
both `tmdb` and `tmdbid` to the same provider for both bracket families) and both are pinned by
name in `tests/test_provider_tokens.py` (`test_b_square_tmdb_bare_tag_token_found`,
`test_e_emby_equals_variant_found`).

### 2. D's committed migration "audit trail" does not match either D's own commit message or the live filesystem

D's `docs/feature-token-brackets/MIGRATION_APPLY_2026-09-07.txt` (650 lines, committed in
`ea91c64`, the *same* commit that adds the Phase‑B/`os.rename`-fallback code) shows three "empty
Friends season" folders (Season 00/09/10) failing with *"No library entries reference … — nothing
to rename"* and then, per the transcript's own text, going straight to *"rename FAILED … continuing
with the rest"* — with **no evidence in the transcript that the just-added `os.rename` fallback was
ever attempted** (no "✅ direct rename" line, no "direct rename failed" line — both would be
unconditionally printed by the code as committed). Yet `ea91c64`'s own commit message says *"3
empty dirs direct-renamed"*, and — independently confirmed by this review with a **read-only**
`Get-ChildItem` against the real `C:\Media` — **all three of those season folders are in fact
`[tmdbid-1668]` on disk today.** So there was at least one more `--apply` run, after the one whose
transcript is committed, that actually fixed those three folders (and, per `PROGRESS.md`,
"The X-Files") — and its output was never captured or committed. The only folder the committed
transcript and today's live filesystem agree on is the one still-locked top-level
`Friends (1994) {tmdb-1668}` folder (`[WinError 5] Access is denied`), which this review confirmed
independently is genuinely still the only unmigrated folder (C's own migration command, re-run
read-only by this review: `scanned=317 would-rename=1 already-canonical=236`, naming that exact
path). **D's headline "ships dry-run/apply transcripts as committed files" claim is real for the
dry-run file, but the apply transcript is a stale intermediate snapshot, not the final record** —
worth knowing if the user was relying on it as a complete history.

On the safety side, D's claim of "zero non-folder_path field changes" independently checked out
well: this review diffed D's own pre-migration backup (`C:\Media\library_backups\imp-u6-2026-09-07\library_series.json`,
894 entries) against the live `library_series.json` (947 entries) in Python. Result: 879
`folder_path`-only diffs (expected — the renames), and exactly **2** non-path field diffs, both on
`tv-en-1994-friends-s08` (`children` grew from 15 to 24 episodes, `total_episodes` 15→24) — clearly
ordinary library growth from unrelated archiving activity in the two weeks since the backup, not
migration-caused corruption. The live-library mutation itself was safe.

---

## Detection model: TMDB-only vs any-provider — the real scope of the difference

The orchestrator's brief framed this as "C is TMDB-specific by design; D is any-provider." On
inspection this is **only true for the artwork-ancestor walk** — both branches keep the
**stamp/idempotency guard TMDB-only**:

- C: `_has_tmdb_token` (`main.py:2013`, thin wrapper over `mvcommon.has_tmdb_token`) — TMDB-only,
  everywhere it's used.
- D: `_has_tmdb_token` (`main.py:206`) — **also TMDB-only** (`_TMDB_TOKEN_RE`, tmdb/tmdbid tags
  only). D's *separate* `_has_provider_token` (`main.py:218`, `_PROVIDER_TOKEN_RE`) is any-provider
  and is used **only** by `_ancestor_show_folder_image`/`resolve_artwork_path` (D's own
  `DECISIONS.md` D3/Consumer-Impact table confirms this split explicitly).

So the real, narrower question is: for the artwork-inheritance ancestor walk specifically, should a
`[tvdbid-…]`-only folder count as "the show folder"? Both loops (`_ancestor_show_folder_image`) do
**not** stop climbing on token-match alone — they only return early if the matched ancestor's
`poster.jpg`/`fanart.jpg` actually exists on disk; otherwise they keep climbing. That materially
limits D's downside: a false-positive requires an *unrelated*, non-MediaVault-tagged intermediate
directory to coincidentally hold its own `poster.jpg`. This review checked the real library for the
two actual `[tvdbid-…]`-only folders that exist today (`Dark (2017) [tvdbid-334824]`,
`Fringe (2008) [tvdbid-82066]`) — **neither currently has a poster.jpg/fanart.jpg at that level**,
so today this design difference has **zero practical effect** on the real library. It remains a
real, latent design choice: D's any-provider walk would let Dark/Fringe inherit a poster the moment
one is dropped there (even before TMDB enrichment runs); C's TMDB-only walk would not, until
MediaVault's own enrich stamps a `[tmdbid-…]` token. Given MediaVault is TMDB-for-everything with
no arr-stack/Sonarr-style workflow in this codebase, the benefit is narrow but real and the
downside (verified) is low. **Recommendation: adopt D's any-provider widening for the artwork walk
only, keep the stamp guard TMDB-only on both branches (already true).**

---

## NFO-at-stamp (D's headline feature) — code-level assessment

`_write_nfo` (pre-existing on `main`, `IMP-U3` down-payment) previously **overwrote** an existing
NFO and was only triggered by an explicit `--nfo` flag. D changes both:

```python
# D, main.py:2439 area — added BEFORE the TMDB-enrichment fetches, so an
# existing NFO costs zero network calls:
nfo_path = os.path.join(folder, nfo_name)
if os.path.exists(nfo_path):
    print(f"     📄 {nfo_name} already exists — kept (never overwritten).")
    return
```
and the trigger, in both `cmd_enrich_metadata` and `_enrich_after_archive`:
```python
if folder and not no_nfo and (write_nfo or will_stamp):
    _write_nfo(...)
```
`--nfo` keeps its old meaning (force, even without a stamp); a new `--no-nfo` opts out entirely;
plumbed through `cmd_prep_push_rep_enrich`/`cmd_prep_push_rep_season_enrich` and the CLI arg
parser. This is **exactly the right shape**: it reuses the existing, unmodified `_write_nfo`
(same element set, same `NEVER raises` contract), checks existence before any network call, and is
tested end-to-end through the real enrich path in `tests/test_token_brackets.py` — default-on
write, `--no-nfo` opt-out, never-overwrite (byte-for-byte preserved across the accompanying
rename), and legacy-curly-folder non-restamp, all four asserted against real filesystem state, not
just mocked. `.nfo` is confirmed absent from `mvcommon.VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi',
'.mov')` (untouched by either branch), so scan/push/dummy stay blind to it structurally — this
isn't a new guarantee D added, it falls out for free from not touching that registry, but it does
hold.

**Failure modes worth naming (not found broken, but worth the user's awareness):** a stale NFO
after a re-identify (change the TMDB id via `set_tmdb` post-stamp) is not auto-regenerated — the
docstring says so ("delete it to regenerate"), consistent with the poster/fanart "local always
wins" rule elsewhere in the codebase, so this is a deliberate, consistent design choice, not an
oversight. NFO-vs-folder-token disagreement (e.g., user hand-edits one but not the other) is
theoretically possible but no more so than the pre-existing poster/fanart-vs-metadata drift this
codebase already tolerates. Whether Plex's NFO agent actually behaves as documented is explicitly
out of scope for this review (another agent is verifying that externally per the task brief).

---

## Migration tool design: standalone script (D) vs in-CLI command (C)

| | C (`cmd_migrate_provider_tokens`) | D (`tools/migrate_token_brackets.py`) |
|---|---|---|
| Discoverability | `python main.py` help text, `main.py:4165` | must know the file exists |
| Smoke-gate coverage | ✅ one smoke test drives dry-run→apply→idempotent-apply | ❌ deliberately excluded (own design goal — keeps CLI surface/smoke contract stable) |
| Ancestor handling | ancestor-walk-up from every physical entry's `folder_path`, unioned/deduped, one pass | two explicit phases: A (library `folder_path`s) + B (`os.walk` scan for on-disk group folders not referenced by any entry) |
| Ordering | deepest-first by path-depth | deepest-first by path-depth (same idea, same reason) |
| Fallback when `cmd_rename_folder` refuses (no library entry references the folder) | not applicable — C's design folds this case into the same ancestor-walk pass, so it always goes through the journaled path | falls back to a raw `os.rename` **outside the rollback journal** for the specific "zero references" case, with an explicit safety argument (nothing to rewrite, so a direct move is equivalent) — reasonable, but is a real, if narrow, departure from "every migration write goes through the crash-safe path" |
| Re-runnable by a future user | yes, either way | yes, either way |
| Audit trail | fresh JSON report per `--apply` run (`remote_bearing` flag, `migration_reports/token_format_<ts>.json`) | committed dry-run + apply transcripts as files — good idea, but (finding above) the apply transcript on disk is stale/incomplete relative to what actually happened |
| IMP-C24 sequential-write discipline | ✅ (single loop, no threads) | ✅ explicit design note, same guarantee |

**Recommendation: C's in-CLI design is the better default going forward** — it's inside the smoke
gate (so a future refactor of `cmd_rename_folder`/detection can't silently break the migration path
without the standing 30-second gate catching it), it doesn't need a second `sys.path` bootstrap
trick, and its dry-run output was independently re-verified to match live reality exactly. D's
standalone-script philosophy (matching `tools/migrate_rehash_flag.py`) is defensible as a
deliberate "don't bloat the CLI surface for a one-shot tool" stance, and its Phase-B on-disk
group-folder scan is a genuinely useful idea C's ancestor-walk doesn't need (C's walk already
covers that case structurally) — but the untracked final-state problem found above is a real ding
against "committed transcripts as the audit trail" specifically.

---

## Shared-helper architecture: `mvcommon.py` (C) vs `main.py`-embedded (D)

C: one parser (`mvcommon.find_provider_tokens`, `mvcommon.py:752`) that every predicate
(`has_tmdb_token`) and every emit site derives from; the old duplicate `_PROVIDER_TOKEN_RE` at the
bottom of `main.py` is **deleted**, not just superseded. Vocabulary extension is one line
(`_PROVIDERS = ("tmdb", "tvdb", "imdb")`). Lives in `mvcommon` specifically so a future module
besides `main.py` (e.g. `tools/*.py`) can import the detector without importing all of `main`.

D: one regex block near the top of `main.py` (`main.py:190`–`238`), also a single source of truth,
also explicitly framed as "the IMP-C23 anti-drift rule," also with the old duplicate deleted. The
architectural difference from C is smaller than it first appears — both close the *structural*
drift risk (two copies of the same regex). The **material** difference is in the *test*
methodology: D's drift-pin tests (`test_has_tmdb_token_agrees_with_tmdb_token_re`,
`test_has_provider_token_agrees_with_provider_token_re`) assert the predicate function agrees with
*its own* module-level regex — which cannot catch a bug in the regex itself, only a wiring
mismatch between the function and the regex. C's tests (`test_provider_tokens.py`) assert exact
expected boolean/list outputs against a curated adversarial input set (bracket mismatches, release
tags, mixed casing, Emby `=`) — real correctness pins, not just self-consistency pins. This
methodology difference is exactly why finding 1 above (D's `[tmdb-…]`/`=` gaps) went undetected on
D's branch and was caught here.

**Recommendation: C's module placement is marginally better (importable by `tools/`), but the
larger, provable win is C's test methodology, which the project's own three-times-bitten history
says matters more than where the regex physically lives.**

---

## Test quality (not just count)

Net-new test-function counts, own contribution only (`git diff … | grep -c '^+def test_'` minus
removals): **C ≈ 32 net new, D ≈ 20 net new** (D's 7 removed tests are all deliberate,
justified pin-flips for behavior D itself reverses — NFO off→on-by-default, NFO
overwrite→never-overwrite, tvdb→tmdb placeholder — each with a direct successor test; verified,
not silent coverage loss). Raw pass counts (938 vs 911) are **not** comparable as-is because C's
number includes ~33 tests merged in from upstream `main` (FLAC carry-out, mkvmerge UTF‑8 fix) that
have nothing to do with IMP-U6; the net-new figures above are the fair comparison.

Both suites are genuinely green (re-executed by this review, not taken on trust): **C 938
passed / 0 failed, D 911 passed / 0 failed**, smoke **C 81 / D 80** (the one extra C smoke test is
exactly the new `migrate_provider_tokens` CLI verb's smoke coverage — D's tool is outside the
smoke gate by design, discussed above).

Who covers the harder cases: **C** — ancestor-only rename (`test_ancestor_only_rename_friends_shape`),
`[rartv]` release-group collision preserved byte-identical, mismatched brackets, idempotent re-run,
dry-run purity (asserts `cmd_rename_folder` is **never called** in dry-run, via monkeypatch), hash/
status safety, multi-level nested ancestors deepest-first, JSON report shape + `remote_bearing`.
**D** — the NFO feature end-to-end (write-by-default, `--no-nfo`, never-overwrite, legacy-non-restamp),
Phase B group-parent folders, uppercase+dedup+preserved-tags, the John-Wick old-keyword-square case
(tool-only, see finding 1), library filter + limit + call-order. Neither suite is vacuous — both
assert real filesystem/library state, not just "no exception raised."

D touched six pre-existing test files (`test_extras.py`, `test_fetch_trivia.py`,
`test_refresh_online.py`, `test_web_detail.py`, `test_web_items.py`, `test_rename_folder.py`) plus
one JS fixture — verified by reading every diff: all are **mechanical fixture-string swaps**
(`{tmdb-…}` → `[tmdbid-…]` in incidental seed-folder names that those tests' actual assertions don't
depend on), cosmetic consistency polish with no behavioral coupling, not coverage gain or loss.

---

## Where they genuinely conflict

**TMDB-only vs any-provider artwork walk.** Already covered above. **Recommendation: take D's
widening for the artwork walk only** (real, if narrow, benefit; verified-negligible risk given the
"only return if the image actually exists" loop structure both branches share).

**Standalone script vs CLI command for migration.** Already covered above. **Recommendation: C's
in-CLI design**, specifically because it's smoke-gated and its dry-run output independently proved
accurate against the live library, while D's committed audit trail proved stale.

**Migrate-now vs migrate-later.** This is not actually a live choice anymore — D already ran the
live migration, seven-plus hours before C's session even started, and this review confirmed the
result is safe (backup diff: only 2 unrelated field changes across 1215+ entries) and matches
today's disk state except for the one locked folder both branches independently agree on. The
forward-looking lesson is procedural, not architectural: **running a live-library mutation from a
feature branch whose code hasn't reached `main` is what created the two-week-old regression on
`main` documented above** — not a flaw specific to either candidate, but a sequencing risk worth
naming for next time (merge the code fix before or immediately alongside any live data migration,
not weeks after).

---

## Take from D into C (ordered, with exact grafts and risk)

1. **NFO-at-stamp (highest value).** Port D's three changes onto C's untouched `_write_nfo`
   (C's `main.py:2013`-adjacent region, the pre-existing IMP-U3 function — confirmed **zero** diff
   to it on C's branch): (a) the never-overwrite existence check at the top of `_write_nfo`,
   copied near-verbatim from D `main.py:2439`+; (b) change the trigger in `cmd_enrich_metadata` and
   `_enrich_after_archive` from `if write_nfo and folder:` to
   `if folder and not no_nfo and (write_nfo or will_stamp):`; (c) add a `--no-nfo` flag through the
   same CLI plumbing points D touched (`cmd_prep_push_rep_enrich`, `cmd_prep_push_rep_season_enrich`,
   the three `elif cmd == ...:` arg-parsing blocks). **Risk: low.** This reverses an existing,
   tested contract (`_write_nfo` used to overwrite; C's suite has no tests pinning that old
   behavior since C never touched this area, so there's nothing to un-pin) — but it changes runtime
   default behavior for every `--apply` enrich run from "no NFO unless asked" to "NFO written
   whenever a folder is stamped." That's an intentional default-behavior change and should be
   called out to the user explicitly at merge time, mirroring D's own D6 ruling process, even
   though the code risk is low.
2. **The `card.js` fix, adapted for C's convention.** C's bug is broader than D's original (it
   affects the series/anime placeholder too, not just movies — verified above). Fix: change
   `card.js`'s `folderNote.textContent` from `"New-item suggestion — edit the {" + field + "-…} id
   before creating."` to bracket form. Because C, unlike D, did **not** unify the placeholder on
   TMDB for series/anime (`provider_field` stays `"tvdb"` there, emitting `[tvdbid-000000]`), the
   correct fix on C is `"edit the [" + field + "id-…] id before creating."` (matches C's own emitted
   tag literally, both `tmdb`→`tmdbid` and `tvdb`→`tvdbid`) — not a direct copy-paste of D's line.
   **Risk: trivial**, one string, no test currently covers this string on either branch (also worth
   adding one while grafting).
3. **Any-provider artwork-walk widening**, as argued above. Graft: change C's
   `_ancestor_show_folder_image`'s `mvcommon.has_tmdb_token(name)` call to a new
   `mvcommon.has_provider_token(name)` (add this as a sibling function next to `has_tmdb_token` in
   `mvcommon.py`, deriving from the same `find_provider_tokens`, exactly mirroring how C already
   structured `has_tmdb_token`). **Risk: low**, verified-negligible false-positive exposure given
   the existing "only return if the image file actually exists" loop guard.
4. **Phase-B-style on-disk group-folder scan as an explicit self-check.** Not strictly needed (C's
   ancestor walk already covers this case structurally, and this review's live re-run proves it:
   `scanned=317 would-rename=1`, correctly finding the one remaining Friends folder without a
   separate disk-walk phase) — but D's `scan_disk` is a good independent cross-check pattern worth
   considering as a *smoke-test assertion* (e.g., "after `--apply`, an independent `os.walk` finds
   zero legacy-token directories") rather than a functional gap. **Optional, low priority.**

## Take from C into D (the reverse graft, for completeness)

1. **Fix the two verified detection gaps** (highest priority if D is ever revived/merged instead):
   widen `TMDB_TOKEN_SQUARE_RE`/`_TMDB_TOKEN_RE`/`PROVIDER_TOKEN_SQUARE_RE` to accept the bare
   `tmdb`/`tvdb`/`imdb` tag (not just the `…id` form) and the `=` separator on the square family —
   i.e., re-derive D's regex from C's `_PROVIDERS`/`_PROVIDER_BY_TAG` design rather than the fixed
   alternation D currently has. **Risk: low-medium** — widening a detection regex used by a live
   idempotency guard needs the same adversarial test coverage C already has (`[rartv]`, mismatched
   brackets) added at the same time, or the fix itself could introduce a new false-positive.
2. **Propagate `_SQUARE_OLD_TMDB` from the migration tool into `main.py`'s core predicate**, so the
   live enrich pipeline — not just the one-shot tool — recognizes a manually-created
   `[tmdb-12345]` folder as already-tokened. Same regex, just needs to live in the shared block
   instead of being tool-local.
3. **Move the migration tool's committed audit trail to a fresh-per-run JSON report** (C's pattern)
   instead of (or in addition to) a hand-committed transcript file, given the verified staleness
   problem. **Risk: none** — additive.
4. **C's adversarial test methodology** — add exact-output pins (not just self-consistency
   drift-pins) for D's predicates, covering the same adversarial set C already has.
5. **Smoke-gate coverage for the migration tool**, if D's "keep it out of the CLI surface" stance
   is kept, at minimum add one `tests/smoke` entry that imports and drives
   `tools/migrate_token_brackets.py` against the smoke fixtures, so a future core-code change can't
   silently break it without the fast gate catching it.

---

## What each missed — even-handed

**C missed:**
- The `card.js` hint bug (D caught it, and D's actual scope was narrower — C's version of the bug
  is slightly worse, affecting both categories, as shown above).
- The NFO-based mitigation for the accepted Plex fuzzy-match tradeoff — documented the tradeoff
  honestly (`DECISIONS.md` D1) but didn't attempt to close it.
- Did not unify the series/anime suggestion placeholder onto TMDB (`suggest_target_folder` still
  emits `[tvdbid-000000]` for series/anime) — arguably correct given C never claims
  TMDB-for-everything as strongly as D's docs do, but it is inconsistent with the fact that
  `enrich_metadata` stamps `[tmdbid-…]` on shows too (C's own `ARCHITECTURE.md:1002` says so) — a
  real, if minor, documentation/behavior mismatch worth fixing regardless of which branch ships.

**D missed:**
- Two real detection gaps in the live enrich pipeline (bare `[tmdb-…]`, Emby `[tmdbid=…]`) —
  verified above, the more serious of the two findings this review surfaced.
- No adversarial test for `[rartv]`-style collisions anywhere in its diff.
- The committed apply-transcript is stale/incomplete relative to what the live library actually
  shows today (verified above) — undercuts the "committed transcripts as audit trail" value
  proposition specifically, though the dry-run transcript and the underlying live-migration safety
  are both genuinely fine.
- Ships an unrelated onboarding doc (`docs/ZCODE_ONBOARDING.md`, 199 lines) and touches three other
  unrelated features' decision docs with addenda (`feature-prep-push-rep-enrich`,
  `feature-web-console`, `feature-web-media-ui`) — the addenda are legitimate, useful cross-doc
  hygiene (each is scoped, dated, clearly marked "supersedes," and factually correct on
  inspection), but `ZCODE_ONBOARDING.md` itself is out-of-scope for an IMP-U6 task and inflates the
  diff.

---

## Blast radius (attributed correctly — C's own contribution only, separated from the merged upstream `main` commits it carries)

```
C, own contribution (562fb4a..3dd57ef + a2085c9..tip, excludes main's FLAC/mkvmerge commits):
  main.py + mvcommon.py (core code):    501 insertions(+), 47 deletions(-)
  tests/:                              1041 insertions(+), 66 deletions(-)
  docs/ + improvements/ (real feature docs): 1336 insertions(+), 62 deletions(-)
  .candidates/ (C's own internal multi-candidate bake-off artifacts, Steps 1+6): 1668 insertions(+)
  19 + 16 files changed total (pre/post-merge parts; some overlap)

D, full contribution (562fb4a..tip, single lineage, not merged with later main):
  main.py (core code):                  172 insertions(+), 81 deletions(-)
  tools/migrate_token_brackets.py:      405 insertions(+), 0 deletions(-)
  tests/:                               661 insertions(+), 151 deletions(-)
  docs (real feature docs, excl. transcripts/onboarding): ~824 insertions(+), 74 deletions(-)
  docs (committed migration transcripts):                1262 insertions(+) [MIGRATION_APPLY + MIGRATION_DRYRUN]
  docs/ZCODE_ONBOARDING.md (unrelated onboarding doc):    199 insertions(+)
  36 files changed total
```

Neither branch touches `ENTRY_TYPE_KEYS`'s dict body or the `RollbackJournal`/PONR/
`RollbackHardFail` definitions — both change-gated surfaces are byte-for-byte untouched on both
branches, confirmed by `git diff` producing zero matching `+`/`-` lines for those symbols on
either side. Per CLAUDE.md's change-gate weighting, this makes both candidates equally safe on the
one criterion that would otherwise be a hard tie-breaker — it does not favor either branch here.

C's raw insertion count is inflated by 1668 lines of its own internal `.candidates/` bake-off
artifacts (CRITIQUE-A/B.md + DECISION.md for its own Steps 1 and 6, which C itself ran as
multi-candidate sub-steps) — this is process bookkeeping, not shipped code or docs, and should be
discounted when comparing "how much did each branch actually change." With that discount, the two
branches' actual code+test+docs footprints are much closer in size than the raw `--shortstat`
numbers suggest (roughly 2878 lines for C vs 2287 for D on the same basis, before D's transcript/
onboarding docs are further discounted).

---

## Recommended path forward

**Ship C.** Before merging:
1. Graft D's NFO-at-stamp feature onto C exactly as described above (highest-value, well-tested,
   low-risk addition; the single biggest thing C is missing).
2. Fix the `card.js` hint bug on C, using C's own emitted-format string (not a literal copy of
   D's line, since C's series/anime placeholder differs from D's).
3. Widen C's artwork-ancestor walk to any-provider (`mvcommon.has_provider_token`), matching D's
   verified-safe design.
4. Merge promptly. Both branches' detection fixes close a live regression that has been open on
   `main` for two weeks (verified above) — every day this stays unmerged is a day the real
   library's artwork inheritance stays broken on `main` and any `main`-based enrich run risks a
   double-stamp.
5. Do not adopt D's standalone-script migration-tool pattern or its committed-transcript audit
   trail as-is — C's in-CLI, smoke-gated, JSON-report design is more robust by this review's own
   testing, and D's transcript was found stale.

This is a recommendation, not a decision — **nothing is merged until the user picks.** Both
branches are legitimate, fully-green, non-trivial pieces of engineering; the gap between them is
real but not enormous, and grafting NFO + the card.js fix onto C closes essentially all of D's
advantage while keeping C's stronger, better-tested detection core.

---

## Confidence and what could not be verified

**High confidence, independently re-executed (not taken on trust from either branch's self-report):**
- Both test suites' pass counts (C 938/0, D 911/0) and both smoke counts (C 81, D 80) — re-run by
  this review in the working tree (C) and a disposable `git worktree` (D, removed after).
- C's live migration dry-run output (`scanned=317 would-rename=1 already-canonical=236
  remote-bearing=1`) — re-run by this review, read-only, no `--apply`.
- The `[tmdb-12345]` / `[tmdbid=12345]` detection gaps in D's core `main.py` — executed D's exact
  regex definitions directly in Python.
- The live-disk state of the Friends season folders and the `[tvdbid-…]`-only Dark/Fringe folders —
  read-only `Get-ChildItem` against the real `C:\Media`.
- D's pre-migration backup vs. live `library_series.json` field-level diff (zero non-path
  corruption) — read-only Python JSON diff against the real files.
- `ENTRY_TYPE_KEYS`/rollback-journal/PONR untouched on both branches — `git diff` grep, both sides.
- Every code-level claim quoted above (shared-helper structure, NFO gate logic, migration-tool
  logic) was read from the actual diffs/`git show` output, not from either branch's own PLAN/
  DECISIONS prose alone (though that prose was cross-checked and, on both sides, found to describe
  the code accurately).

**Could not fully verify / explicitly out of scope:**
- Whether Plex's NFO agent (PMS 1.43+) actually behaves as D's docs describe — explicitly deferred
  to a separate web-verification agent per the task brief; this review judged only the code.
- The exact intermediate history of D's live migration between the committed
  `MIGRATION_APPLY_2026-09-07.txt` and today's live-disk state (there was clearly at least one more
  `--apply` run; its console output was never captured/committed, so its exact content is
  unrecoverable from the repo — only its *result*, which this review confirmed independently via
  the live filesystem and the library backup diff).
- D's own test suite was executed in a disposable worktree using the main repo's shared `.venv`
  rather than a from-scratch environment; this matches how C's suite was run (the normal working
  tree) and is not expected to introduce any discrepancy, but is noted for completeness.
- This review ran on the primary model tier (RUNNING AS `claude-fable-5-1`), not the Opus
  model-fallback path — no re-judging on a different tier is needed for this comparison.


---

# ADDENDUM — 2026-09-22: what the competing branch caught that this one missed

The user asked for this to be written down explicitly, so it is not repeated. Recorded honestly,
including the parts that reflect badly on this branch's process.

## 1. `webui/static/card.js` — a real bug, missed

The web console's new-item hint read *"edit the `{provider-…}` id before creating"* while
`suggest_target_folder` had already been changed to emit a square-bracket placeholder. The UI
literally contradicted the value it was displaying.

**Why it was missed:** Step 3's consumer audit checked `webui/server.py` and `webui/static/preview.js`,
correctly ruled `preview.js` out (its "tmdb" hits are the ratings-hyperlink feature), and **stopped
there**. `card.js` was never opened. The audit searched for the token *pattern* (`{tmdb-`) rather than
for every place the folder-suggestion feature surfaces to a user — and this string builds the token
from a variable, so no pattern search would ever have found it.

**Practice change:** when a feature's output format changes, enumerate the feature's **consumers**
(who renders or parses this?), not just textual matches of the old format. A grep finds literals; it
does not find a UI that assembles the literal from parts.

## 2. `suggest_target_folder` — right call made for the wrong reason, late

DeepSeek unified the new-item placeholder on TMDB for movies, series **and** anime. This branch kept
`[tvdbid-…]` for series/anime, because the pre-existing code did.

DeepSeek was right on a ground this branch had all along: MediaVault is TMDB-for-everything and
**refuses `-tvdbid` outright** (IMP-D22, a different id space). Offering a TVDB placeholder invited
the user to type an id the tool can never resolve. That is knowable from the codebase alone; it did
not need a server test. This branch only changed it after the judge flagged it and the live test
independently confirmed TMDB works for TV.

**Practice change:** "the existing code did it this way" is not a reason. When touching a default,
check whether the rest of the system can even honour it.

## 3. The Plex question — the deepest miss, and it was a reasoning failure

DeepSeek added NFO sidecars specifically to keep Plex matching after the move to square brackets, and
was **factually correct** that Plex ships a first-party NFO agent (`Plex NFO Movie` / `Plex NFO
Series`, PMS 1.43.1+, February 2026). This branch's initial position was that no such first-party
agent existed — stale knowledge, stated with more confidence than it deserved.

More importantly, the two branches faced the same problem and responded differently:

- DeepSeek saw "Plex won't read this" and **tried to solve it**.
- This branch saw "Plex won't read this" and **documented it as an accepted tradeoff**.

The tradeoff turned out to be unnecessary. Empirical testing later showed `{tmdb-<id>}` is read by
Plex, Emby *and* Jellyfin — the premise that no single format satisfies all three was simply false.
This branch verified the half of the premise that confirmed the plan (Plex rejects `[tmdbid-]`) and
never tested the half that would have overturned it (do Emby and Jellyfin accept braces?). Documenting
a tradeoff felt rigorous; it was actually the cheaper option than questioning whether the tradeoff had
to exist.

**Practice change:** when a decision rests on "X doesn't support Y", test the symmetric claim too
before building around it. A constraint accepted without testing its converse is an assumption
wearing a constraint's clothes.

## 4. Where this branch was genuinely stronger — for balance

Independently verified by the judge, not self-reported:

- **Detection completeness.** DeepSeek's live enrich guard `_has_tmdb_token` does not recognize
  `[tmdb-…]` or `[tmdbid=…]`; those spellings are handled only inside its standalone migration
  script. That is the same double-stamp class (IMP-C18/C22/C23) this feature exists to close.
- **Shared helper in `mvcommon.py`** with the duplicate regex deleted, so drift is structurally
  impossible rather than tested-for.
- **Audit-trail accuracy.** DeepSeek's committed migration transcript disagreed with both its own
  commit message and the live filesystem. This branch's dry-run output was re-run and matched reality
  exactly.
- **Tests that were mutation-verified** rather than assumed — flipping the deepest-first sort and
  restoring the old brace-only predicate both produced the expected failures.

## 5. The one-line summary

DeepSeek was better at **questioning the brief**. This branch was better at **executing and verifying
it**. The bugs this branch missed were all in the same category: consequences of a change in places
the change's own pattern does not appear.
