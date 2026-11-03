STATE: IN PROGRESS

# IMP-C25 follow-up — map_photos reporting: the dry-run preview counts what --apply changes; an item uploaded after the crawl is not "not-listed" (mpfix) — resumable status

- User decision 2026-10-28 (card): "Fix both inside C25 (Recommended)" (DECISIONS.md, 2026-10-28 "map_photos reporting
  fixes"). Two fixes, map_photos' reporting only: (1) the dry run's apply preview over-counts (after the R3 apply the
  `--repair split-info` dry run printed `library (dry-run): 2,207 object(s) would change (0 newly confirmed)`; the
  apply then changed only the 6 chunks of the 3 repaired entries); (2) `not-listed` fired for an item uploaded after
  the account's crawl began (Aavesham chunk 1/2) and its text suggests archive / trash.
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-mpfix` · branch `tmp/c25_mpfix` · base 12dd2cf. The main folder,
  the real library, C:\Media, D:\, Chrome and Google are never touched. No live runs; nothing real read.
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe -m pytest …`, run from the worktree.
  Gates SEQUENTIAL (full suite, then smoke), pytest's default basetemp, no xdist (low memory). Baseline (dispatch) at
  12dd2cf: full 3409 · smoke 102. Known load flake: tests/test_gpweb.py::test_fake_delayed_download_completes_in_the_background
  (re-run it alone before calling it a regression).

## Resume rule
Read this file, then `git log --oneline -12` (WIP commits `wip(imp-c25): mpfix.<x> — …`) and `git status`. An
uncommitted diff belongs to the first unticked sub-task: keep it when coherent and its targeted tests pass, else
`git checkout -- <its files>` (record that here). Commit THIS file (force-added: `.candidates/` is gitignored) with
every WIP commit; tick a sub-task only after its tests pass, with its SHA. Pathspec commits only; never -A, amend,
rebase or stash; `git diff --cached` + the secrets scan before every commit. Never edit PROGRESS.md / PLAN.md /
DECISIONS.md / PRIORITY.md (the orchestrator's). check_photos' apply (`_cp_apply`) shares `_mp_append_new`: its
behaviour must stay identical ((appended, known) counts and append order).

## Sub-tasks (one WIP commit each)
- [x] mpfix.0 this file: the findings, the design decisions, the sub-task list (no code) — 4b3d187
- [x] mpfix.a Fix 1 — the preview counts what --apply changes (D1–D5): main `_mp_new_records` (`_mp_append_new` uses
      it — map's and check_photos' applies unchanged), `_mp_run_records` (what an apply records: records, hints, names —
      `_mp_apply` and the preview), `_mp_proven_plans`, `_mp_patch_split_info` (extracted from `_mp_write_split_info`
      unchanged), ctx `index`, `_mp_repair_preview`, `_mp_preview` (the dedup; the whole-sidecar engine even when the run
      records nothing; the repaired objects left to the repair line), `_mp_apply_lines` (the repair line); docstrings
      (+ cmd_map_photos' `preview` contract). Tests +10 (test_map_photos; `_split_lost` gains `flags`): a dry run after
      an apply previews 0 (the clock moved: the records are re-dated) and the next apply changes nothing · a new fact
      previews exactly the change its apply makes (field for field) · `--repair names` and resolutions preview their
      change, then 0 after their apply · the repair line (after the map apply: 0 + 2 chunks = the apply's 2, the exact
      three lines; a scope with nothing else: still previewed, `nothing-to-apply` + the line + `Next:`) · the repair line
      counts only chunks that will map (a partial-upload chunk: 1 of 2) · an unfolded record about the repaired whole
      file is not counted (the apply orphans it) · a run that records nothing still previews the sidecar's pending change
      · `_mp_new_records` / `_mp_append_new` unit. Runs: map + check + apply engine + resolve + pins + primitives 744
      passed · smoke 102 passed — 22fbd4c
- [x] mpfix.b Fix 2 — `uploaded-after-crawl` (D6–D7): main `_mp_after_crawl` (the evidence text, used by the verdict
      and the summary line alike), `_mp_nowhere`, `_mp_recency` (leaves it alone), MAP_STATES (after not-listed), the
      summary block, docstrings / section comment / cmd_map_photos' status list. Tests +8 (test_map_photos): the R3
      Aavesham case (verdict, note, the block and its line, no archive / trash text, off todo / inconsistencies / LOUD
      block, the Verdicts line, mapping.csv) · upload_ms at the crawl start vs 1 ms before · no upload_ms: the push
      time after / at the crawl start vs 1 s before (a filename-proof record: `_mp_recency` must leave it) · a
      confirmation after the crawl began (verified_at) proves nothing · --apply leaves its block byte-equal, no learned
      record, no failed row. All 6 added lines over 120 characters reflowed (none left). Runs: map + check + apply
      engine + resolve + pins + primitives 752 passed · smoke 102 passed — a265d8c
- [x] mpfix.c registry pins + docs, every claim checked against the code (and the R3 numbers against PROGRESS.md).
      Pins: tests/test_c25_regression_pins.py +15 (the 5 uploaded-after-crawl tests, the 10 preview tests) + its
      docstring (the not-listed entry gains uploaded-after-crawl; a new entry "map: the dry-run preview counts what
      --apply changes"). README (map_photos): the uploaded-after-crawl exception + a paragraph on the apply preview and
      its repair line. ARCHITECTURE §8.10: step 4 "And `uploaded-after-crawl`" (`_mp_after_crawl` `main.py:11704`) + a
      paragraph "The dry run's apply preview" (`_mp_preview` `main.py:12772`); §13 test row; Last updated (thfix's
      note -> Earlier). OPERATIONS_QA §6d: new Q&A "The map dry run says "N object(s) would change" — will `--apply`
      really change that many?"; the possible-loss Q&A names uploaded-after-crawl; the not-listed Q&A gains the
      Aavesham paragraph (what the user sees, why verified_at proves nothing); Last updated. Line refs: the main.py refs
      this change shifted (+1 at the map constants, +7 at `_mp_unlisted`, +113 at cmd_map_photos, +118 after the map
      section) re-pointed with scratchpad `mpfix/refs_main.py` (difflib line map base -> tree; `main.py:N`, `:N` after
      it, the "`name` (N)" lists): ARCHITECTURE 31 refs (§5, §6.5, §8.10, §10 Stage 4, §11, §14, §18), OPERATIONS_QA 2,
      README none; every re-pointed line checked to hold its named def / constant; thfix's `refs_verify.py` forward
      check: 339 paired refs, 0 mismatches, the 2 new refs (and the re-aligned check_photos line) anchored right. Left
      as they are (point-in-time): ARCHITECTURE's "(18294 lines as of 2026-10-20)" notes. Only this file cites base
      line numbers elsewhere (its Findings: main.py at 12dd2cf). Runs: pins + map 241 passed — this commit (its SHA is
      recorded with mpfix.d)
- [ ] mpfix.d disarm probes on an isolated `git archive` scratch copy: each part reverted on its own turns a test red;
      every file restored byte for byte (SHA-256 checked).
- [ ] mpfix.e gates: full suite, then smoke; the secrets scan over the whole range; the self-review; STATE: COMPLETE;
      the final commit `fix(imp-c25): map_photos — the dry-run preview counts what --apply changes; an item uploaded
      after the crawl is not "not-listed"`.

## Findings (code read 2026-11-03; synthetic data only; line refs = main.py at the base 12dd2cf)
- F-a The over-count. `_mp_preview` (main.py:12664) gave the engine `existing + records`, where `records` are this
  run's records stamped `at` = now (`_mp_record` -> `_normalize_learned(fill=True)`); the apply appends only the records
  whose `_mp_fingerprint` (every key but v / at) is not on record (`_mp_append_new`, main.py:12605) and then runs the
  engine over the sidecar. In the preview every re-dated duplicate wins its group (`reduce_learned`: proof strength,
  then the newest `at`) and `apply_learned` sets `verified_at` = its `at` -> the block changes -> counted. Real (R3,
  2026-10-28, PROGRESS.md): the dry run previewed 2,207 changes, 0 newly confirmed; `--apply` then recorded 6 new
  records (2,225 already on record), repaired 3 entries and changed their 6 chunks (6 newly confirmed). The correct
  preview: 0 + the repair's 6 chunks.
- F-b Why no test saw it: the test world pins `mvcommon._now_utc`, so a second run stamps the same `at` and a duplicate
  changes nothing. The new tests move the clock between the apply and the dry run.
- F-c Other ways the preview drifted from the apply: (1) it returned None — no preview, no `Next:` line — when the run
  itself had no record and no names fact, but the apply always runs the engine over the WHOLE sidecar (a record
  appended earlier and never folded changes the library with no preview line); (2) `--repair split-info`: the apply
  writes the proven split_info FIRST and maps again (`_mp_apply`, main.py:12690): the repaired whole files vanish
  (P-3: a split leaf yields only its chunks — their records become orphans) and their chunks appear and map. The
  preview counted the whole files' records (e.g. a url resolution for a split-info-lost row, or an unfolded record:
  a change the apply never makes) and could not see the chunks; (3) the resolution condition differs in text only
  (`ctx["resolution"]` vs `… and not fatal`: a fatal resolution has no learned records) — one source now (D2).
- F-d Not affected: resolve_photos' preview (`_rp_apply`) reads the sidecar after its answers were appended — already
  the apply's exact input; check_photos has no engine dry-run preview. `_mp_engine_line` is shared with
  resolve_photos, so the repair's effect is a line of its own (the engine line unchanged).
- F-e The not-listed case. `_mp_unlisted` (thfix, main.py:11677) returns a confirmed record whose photo_id the fresh
  crawl does not list; nothing compares when the item reached Google Photos with when the crawl began. Aavesham chunk
  1/2 (dispatch): pushed 2026-10-27 18:46Z, the movies crawl began 2026-10-28 07:48Z, a check confirmed it ~08:00Z ->
  the next map dry run: `not-listed … (archived, in the trash?)`.
- F-f Facts available on such a row: the record's item facts (`upload_ms`, copied into the row's item), its
  `verified_at`, and the capture's push time (`pushed_at`). `mvcommon.apply_learned` sets `confirmed` only for a
  photo_id with an int `upload_ms`, a verified proof and a confirming source — so every confirmed record the engine
  wrote carries `upload_ms`.

## Design decisions (binding — recorded first so a resumed session stays consistent)
- D1 One rule for "not on record yet": `_mp_new_records(records, existing)` -> the records whose fingerprint (all keys
  but v / at) is neither in `existing` nor earlier in `records`. `_mp_append_new` appends exactly those (map's apply AND
  check_photos' `_cp_apply`: same counts, same order); the preview adds exactly those to the sidecar.
- D2 One source for what an apply records: `_mp_run_records(ctx, repairs, accept_probable)` -> (records, hints, names):
  the rows' learned records (`_mp_learned`) + the user's resolution records when the file was read (`not fatal`), the
  resolution hints, and the `--repair names` facts. `_mp_apply` and `_mp_preview` both call it.
- D3 The preview runs the engine over the whole sidecar + D1's new records + the names facts — like the apply — also
  when this run records nothing. None (no preview section) only when the engine has nothing to apply AND no repair is
  pending (today's "nothing to apply"). Visible change: such a dry run now shows `Apply preview` when the sidecar is
  not empty (0 or more changes — what the apply's engine would really do).
- D4 The split-info repair's own effect (the decision the dispatch asks for). A dry run cannot write, so
  `_mp_repair_preview` patches a COPY of the repaired entries with the writer's own patch step (`_mp_patch_split_info`,
  extracted unchanged from `_mp_write_split_info`) and maps those entries again with the run's own inventory index and
  freshness (`_mp_match`, the apply's matcher; nothing read twice, never a phone). Shown as its own line under the
  engine line:
  `  + N chunk(s) of M repaired entr(y/ies) map after the repair — --apply writes their split_info first, then records them (not in the count above)`.
  N = the repaired entries' chunks whose verdict records something (`_mp_learned` takes the row): each is a node the
  repair creates, so each gets a block (one change) in the apply. Never the plan's total_chunks — a chunk that will
  not map (a partial upload, a push newer than the crawl, …) is not counted. M = the distinct entries repaired.
  Never larger than the apply: the engine line leaves out every record about the repaired objects — their whole files
  (gone after the repair; the apply orphans those records) and their chunks (not objects yet: orphans in the preview,
  folded by the apply) — and N counts only chunks that will change; chunks that only old sidecar records or a
  resolution name can only make the apply's count larger.
- D5 The command result's `preview` stays the engine's report (same keys); with `--repair split-info` and a proven plan
  it gains `repair: {entries, chunks}`.
- D6 Which facts say "uploaded at or after the crawl began" (Fix 2). The item's own `upload_ms` decides whenever the
  record has one (every confirmed record does — F-f): at or after the crawl start -> not-listed does not apply; before
  it -> not-listed, even when the same bytes were pushed again later (the thfix test of a re-push after the crawl keeps
  pinning this). Without `upload_ms`: the capture's push time at or after the crawl start (a push reaches Google Photos
  only after it was made — a lower bound of the upload). NOT `verified_at`: it is an upper bound (the item was there
  when verified), so it cannot show the item arrived after the crawl began — using it would turn an item archived
  before the crawl and confirmed by photo_id after it into "the next crawl lists it", the very case not-listed exists
  for. Compared to the second (the crawl start is kept to the second). The account = the confirmed record's account.
- D7 The treatment: a distinct verdict `uploaded-after-crawl` — not `mapped` (a mapped row is applied: `_mp_learned`
  would re-record its item with source map and the engine would re-date its block; the apply must change nothing).
  The name (the dispatch's example was `listed-after-crawl`) names the fact that decides: Google Photos received it
  after the crawl began. Handling mirrors not-listed: no failed reason (off the failed list and resolutions_todo.csv),
  not in the LOUD block or inconsistencies.csv, nothing recorded by --apply, a partial-set sibling counts it present,
  `_mp_recency` leaves it alone (its confirmed record says where it is), MAP_STATES right after not-listed. Texts:
  note (mapping.csv) `in '<a>' per its confirmed record — uploaded after this crawl began (uploaded <t>, the crawl
  began <t>); the next crawl lists it` (`pushed <t>` when the push time decided); summary block
  `Uploaded after the crawl began — NOT a loss: N object(s) whose confirmed record names an item that reached Google
  Photos after its account's latest crawl began — the next crawl lists them; nothing is recorded for them`, then per
  object `    uploaded-after-crawl <id> · <label> — in '<a>' per its confirmed record (uploaded <t>, the crawl began <t>)`.
  Times `YYYY-MM-DDTHH:MM:SSZ`. Never the words archived / trash. not-listed's own texts unchanged.

## Assumptions (surfaced — the dispatch does not fix these)
- A1 The real Aavesham record's `upload_ms` is at or after the movies crawl start (2026-10-28 07:48Z): the dispatch
  says the crawl ran "before Google Photos had it". Not checked — the real library is off limits. Were its upload_ms
  earlier (an item uploaded but not yet listable), it would stay not-listed by design (D6).
- A2 (deviates from the dispatch's literal list "else its verified_at, or the capture's push time") verified_at is not
  used (D6) — within its "decide which facts are reliable".
- A3 The verdict name `uploaded-after-crawl`, not the dispatch's example `listed-after-crawl` (D7).
- A4 D3's visible change (a dry run that records nothing previews the sidecar's pending changes).
- A5 The repair line counts entries (distinct ids): two repaired extras items of one title are 1 entry.

## Log
- Model / attribution: dispatched to executor-fable (Fable 5.1), no MODEL-FALLBACK banner; the harness attribution
  named Claude Fable 5.1 and Claude Opus 5.5 at times — every commit carries the trailer the harness gives when it is
  made.
- 2026-11-03: resumed by the coordinator after a usage limit (no commit, clean tree, no STATUS.md); the reading of
  2026-10-28/29 (main.py map section, mvcommon reduce/apply, the map tests, docs) was intact in this session's
  context — this file records it before any code.
