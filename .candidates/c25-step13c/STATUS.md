STATE: COMPLETE

# IMP-C25 Step 13c — `mvmeta_upgrade`: v2 sidecars for the existing library (OD-4 ruling) — resumable status

- Plan: docs/feature-fetch-datetime/PLAN.md Step 13c (+ Step 5 write_remote_mvmeta v2, Step 8 device_inventory /
  `_dev_argv_readonly`, P-1/P-2/P-5, OD-4) · DECISIONS.md §1 (G0 q2 answered 2026-09-28: "Yes, with --apply only")
  and §1d (HT74D0202839 = an extra movies phone, device-check only) · PROGRESS row 8 note: Step 13c must NOT widen
  `_dev_argv_readonly`; its pull/push/mv needs its own --apply-gated path · Step 13's contract (a62e7d4):
  `verified_by` dedupkey only for content-keyed objects, legacy stays filename, a check never writes a block-level
  sha1 / dedup_key.
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-s13c` · branch `tmp/c25_step13c` · base 69087b5 (Steps 0–13b
  done). The main folder is never touched (the user archives from it).
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe -m pytest …`, run from the worktree.
  Gates run SEQUENTIALLY. Baseline re-run here at 69087b5: full suite 2900 passed (266 s) · smoke 82 (orchestrator).

## Resume rule
Read this file, then `git log --oneline -15` (WIP commits: `wip(imp-c25): step 13c.<x> — …`) and `git status`. An
uncommitted diff belongs to the first unticked sub-task: keep it when coherent and its targeted tests pass, else
`git checkout -- <its files>` (record that here). Continue at the first unticked sub-task. Commit THIS file
(force-added: `.candidates/` is gitignored) with every WIP commit; tick a sub-task only after its tests pass, with its
SHA. Pathspec commits only; never -A, amend, rebase or stash. Never edit PROGRESS.md (the orchestrator does).

## Sub-tasks (one WIP commit each)
- [x] 13c.0 this status file: the sub-task plan + the design decisions below (no code) — 17b801e
- [x] 13c.a main: the PURE core — `_mu_facts(obj)` (the proven v2 facts of one uploaded object), `_mu_decode` /
      `_mu_encode` (the sidecar's exact bytes: json.dump indent=2, ASCII, the file's own line ends), `_mu_upgrade`
      (validate + append the missing proven keys) and the byte-identity check — tests: facts rules (sha1 never from
      filename / manual proof, untrusted block ignored, date_utc ladder, account rule, conflicts), v1 keys
      byte-identical, version handling, canonical-form refusals, present-value conflicts — also `_mu_owners` (the
      sidecar owner: an entry or an extras item, FLAC holders left out) and `_mu_taken_utc`. tests/test_mvmeta_upgrade.py
      57 passed (every sidecar produced by the REAL write_remote_mvmeta, at version 1 and at today's 2; confirmed on
      this machine: CRLF line ends, ASCII \u escapes, decode + re-encode exact); neighbours (test_gp_capture,
      test_device_inventory, test_entry_schema_guard, test_gphotos_primitives) 502 passed; smoke 82 passed — f193fde
- [x] 13c.b main: the device path — `_mu_argv_ok` (its OWN allowlist: pull; with --apply push `<sidecar>.tmp` + `shell
      mv` tmp -> sidecar), `_mu_run`, `_mu_pull`, `_mu_write` (keep the original locally -> push .tmp with the
      original mtime -> mv -> pull back + compare -> stat the mtime) — tests with a directory-backed fake adb:
      ordering, -s pins, refusals, a dry run cannot write, failure at every stage, the original kept, the mtime kept,
      `_dev_argv_readonly` NOT widened — also `_mu_mtime` (Step 8's read-only stat through `_dev_run`),
      `_mu_new_run_dir` (bounded, never another run's folder — a first draft could loop forever when a parent was a
      file; fixed before its test) and `_mu_keep_original` (originals/NNNN.mvmeta.json opened "xb" + backup.jsonl, both
      fsynced). `_mu_write` refuses up front when ctx apply is False (a dry run cannot even keep a local copy), the
      allowlist's `write` flag being the second layer. tests/test_mvmeta_upgrade.py 117 passed (+60: the fake adb —
      a real folder per phone, THIS module's own allowlist incl. Step 8's device-shell word splitting, violations
      fail the test at teardown; allowlist 7 allowed + 2 dry-run refusals + 30 refused shapes, the read-only allowlist
      not widened ×3, a refused argv never runs, the write order on a hostile name ($ ` ; ' unicode), 8 failure
      stages each saying what the phone holds, the dry-run ctx, no local copy -> no phone write, the run folder,
      pull failures, the read-only mtime); neighbours (+ test_cmd_push_partial, test_cmd_push_mock_device) 341
      passed; smoke 82 passed — 269b509
- [x] 13c.c main: `cmd_mvmeta_upgrade(scope, device, apply)` (scope like map_photos, phones, owners from
      iter_uploaded_objects, sidecars located by Step 8's classify_device_objects, plan / write loop, report, the
      disabled mode, Ctrl+C) + `parse_mvmeta_upgrade_args` + the CLI branch + the usage line — tests end to end: the
      dry run writes nothing anywhere, --apply, offline / unauthorized / not-connected / placeholder phones, the
      check-only phone, scopes (id, alias, season prefix, extras, unknown), the no-sidecar list, idempotent re-run,
      the dispatcher — also `_mu_phones`, `_mu_locate`, `_mu_row` (one row shape for planned and not-reached
      sidecars), `_mu_plan`, the report helpers; the transcript (every printed line) is the run's report.txt; a row
      interrupted mid-write is marked failed. tests/test_mvmeta_upgrade.py 161 passed (+44 end to end on a 12-item
      world over all four prefixes — whole, chunks + FLAC holder, extras whole + split, a season's extras, an alias,
      a re-mastered entry, a never-uploaded one — on movies / series / the check-only movies#2 phones with media and a
      foreign file beside the sidecars; every sidecar from the REAL writer: the dry run (rows, keys per row, nothing
      written to the library / state folder / system temp / phones, only devices / find / stat / pull, every argv
      but `adb devices` with -s, the exact pull sequence, the report and Next lines), --apply (exactly the five
      rewritten, media / other files untouched, no .tmp left, every v1 byte by THIS module's own check, the mtimes
      kept, the proven values, the per-sidecar pull -> push .tmp -> mv -> pull -> stat order on its own phone, the
      originals + backup.jsonl, report.txt == the transcript, nothing else in the state folder, the library only
      read), a second apply writes nothing, the check-only phone by alias and by serial, unplugged / offline /
      unauthorized phones, an unconfigured attached phone, the disabled mode (no library read, no adb; the dry run
      unaffected), 5 scopes + unknown + nothing uploaded, > 20 without a sidecar, the first failure stops + a re-run
      finishes (its leftover .tmp replaced, a -2 run folder), Ctrl+C mid-write / in a dry run, a leftover .tmp, an
      unreadable mtime, a name two items claim, no key value / photo id / web marker printed, the parser ×12, the
      dispatcher ×7); neighbours (test_device_inventory, test_gp_capture, test_map_photos,
      test_prep_push_rep_enrich_cli, test_cli_parsers, test_auto_confirm, test_entry_schema_guard) 821 passed; smoke
      82 passed — 0e05611
- [x] 13c.d round trip through the REAL writers: v1 sidecars from write_remote_mvmeta at version 1, v2 from today's
      push (cmd_push / push_title_extras on the mock device), a confirmation through the real apply engine, then
      mvmeta_upgrade over the same phone — all four prefixes, chunks + FLAC holder, extras whole + split, an alias —
      tests/test_mvmeta_upgrade.py 162 passed (+1: the REAL cmd_prep / cmd_push (resume branches, captures on) at
      version 1 for a tv split + holder, an others split, an anime whole file; today's cmd_push + push_title_extras at
      version 2 for a movie title + its extras whole and split; every real sidecar in the writer's exact form; the dry
      run upgrades the three v1 ones from their captures (the tv chunks' SHA-1 known from the IMP-C32 resume re-hash;
      the holder never counted) and finds the three v2 ones up to date, writing nothing; the REAL gphotos_apply
      confirms the anime (dedupkey) and the movie (filename) -> --apply adds sha1 / dedup_key / date_utc /
      uploaded_name / gphotos_account to the anime and only date_utc + gphotos_account to the movie (a name match
      proves no content key), every v1 byte and push time kept, chunks / holder / media / other sidecars untouched;
      a re-run writes nothing; the alias id finds its primary's sidecar); neighbours (test_device_inventory,
      test_gp_capture, test_gphotos_apply, test_extras, test_cmd_push_mock_device) 492 passed; smoke 82 passed —
      1e023fa
- [x] 13c.e gates: disarm probes (isolated scratch copy) · full suite · smoke · live read-only dry run (only if
      `adb devices` lists a phone; counts only) · self-review · STATE: COMPLETE · final feat commit — self-review
      first (whole diff, below), its two fixes committed as 6e06df5 (after an --apply that held back a check-only
      phone the exact command to rewrite it is printed; an interrupted run ends with the re-run line; both pinned);
      disarm 38/38 caught on a `git archive` copy of 6e06df5 (scratchpad disarm_13c.py, results per probe in
      disarm_13c_results.jsonl; the copy 162 green before and after, its main.py restored byte for byte; the worktree
      never modified): P1 sha1 from a filename proof (the PLAN's named probe — caught by 4 tests: unit facts, unit
      upgrade, end-to-end apply, the real-code round trip) · P2 from a manual proof · P3 date / account from an
      unconfirmed record · P4 the cloud time beats the file's DateUTC · P5 / P6 disagreeing keys / DateUTCs ignored ·
      P7 a present value overwritten · P8 other bytes upgraded (the P-2 hash check) · P9 another entry's sidecar · P10
      the version not raised · P11 an unknown version accepted · P12 the exact form not required · P13 the
      byte-identity check disarmed · P14 the dry run reaches the write path · P15 the dry run REALLY writes (all three
      layers off — caught by the end-to-end dry-run test itself and the dry-run Ctrl+C test) · P16 the allowlist's
      write flag ignored · P17 a phone command without -s · P18 push straight onto the sidecar · P19 mv before push ·
      P20 no read-back · P21 the mtime not kept · P22 no local copy · P23 the run goes on after a failure · P24 a
      check-only phone rewritten unnamed · P25 `_dev_argv_readonly` widened · P26 holders counted · P27 nothing-proven
      pulled anyway · P28 an unknown push time rewritten · P29 a name two items claim · P30 the disabled switch
      ignored · P31 the temporary folder left · P32 another run's folder reused · P33 / P34 the allowlist's mv / pull
      widened · P35 an interrupted write left "would upgrade" · P36 the account guessed · P37 uploaded_name derived ·
      P38 the post-write mtime check dropped. Full suite 3062 passed (= 2900 + 162), 1 warning (the pre-existing httpx
      deprecation), 458 s, on 6e06df5 with the tree clean; smoke 82 passed (20 s) after it; `--collect-only` 3062.
      Live read-only dry run: NOT run — `adb devices` listed no phone at the gate (checked twice, 2026-10-14).
      Hermeticity: the real ~/.mediavault/gphotos has no mvmeta folder, no mv_mvmeta_* temp folder is left, the real
      C:\Media\library_*.json keep their 2026-10-11 23:00 mtimes. The final commit is the feat commit that sets
      STATE: COMPLETE

## Design decisions (binding for the rest of this step — recorded early so a resumed session stays consistent)
1. Scope = map_photos' rule: `all` | an id | an id prefix (a season id selects its episodes; an alias resolves to its
   primary through mvcommon.iter_uploaded_objects, which is alias / season_map safe). The unit is the SIDECAR OWNER:
   one entry (its whole file or its chunks) or one extras item — write_remote_mvmeta's unit. FLAC holders are not
   listed in any sidecar and are left out.
2. Which sidecars: every uploaded entry / extras item whose sidecar is on a listed phone — whole files too (their
   sidecar is a one-chunk list; Step 5 writes the same v2 keys for them), any cloud-bearing status (iter_uploaded_
   objects' "uploaded"). The PLAN text says "archived split entries"; the user's OD-4 ruling says "you can change all
   existing library also" — recorded as deviation D1 (surfaced in the report).
3. Proven facts only (PLAN P-5, Step 13's contract), each pinned to the chunk's OWN SHA-256 (P-2):
   sha1 / dedup_key — the capture (hashed at prep / push), the TRUSTED block's own content key (only a capture or a
   download writes it), or an account record verified by `dedupkey` / `sha256`; NEVER `filename` / `manual`.
   date_utc — the capture's / the trusted block's (the file's DateUTC), else the taken time (epoch ms, floored to the
   second — check_photos' rule) of the CONFIRMED record of gphotos_account. uploaded_name — the trusted block's (a
   capture's or a proven `--repair names`), else the capture's; never derived. gphotos_account — the block's
   home_account when that account's record is confirmed, else the only confirmed account; none when ambiguous.
   Disagreeing content keys or DateUTCs -> the sidecar is left as it is (reported).
4. The sidecar must be EXACTLY the owner's: manual_id (entry id, or `<title>::<group_rel>/<sub_rel>` for an extra),
   short_id, and its chunk hashes == the owner's current SHA-256s in order; version 1 or 2 (MVMETA_UPGRADE_VERSION =
   2 == MVMETA_SCHEMA_VERSION, pinned). A present key that disagrees with a proven value -> left as it is. Missing
   proven keys are APPENDED to their chunk in MVMETA_UPGRADE_KEYS order (gpcapture.MVMETA_KEYS + gphotos_account);
   a v1 sidecar that gains a key becomes version 2; nothing else changes. Nothing to add -> no device write.
5. Byte identity: a sidecar is rewritten only when its bytes are exactly what write_remote_mvmeta writes
   (json.dump(indent=2), ASCII, CRLF when written on Windows) — then the new bytes minus the added keys (version
   restored) encode to the ORIGINAL bytes exactly; checked before any write (else left as it is).
6. Phones: `device <alias|serial>` names one (device_check_aliases() alias, one of its serials, or any serial); without
   it every phone the device check lists is read. --apply rewrites a NAMED phone, and without `device` only the push
   phones (DEVICE_ALIASES) — a check-only phone (DEVICE_CHECK_EXTRA_SERIALS, §1d) is listed with "name it to rewrite
   it". Offline / unauthorized / not-connected / placeholder -> one line, skipped.
7. adb: listing = Step 8's device_inventory (devices / find / stat — `_dev_argv_readonly`, unchanged); its OWN
   allowlist `_mu_argv_ok` for `pull` (dry run too) and, with --apply only, `push <local> <sidecar>.tmp` and `shell mv
   '<sidecar>.tmp' '<sidecar>'`; every argv but `adb devices` carries `-s <serial>`; the `write` flag is the run's
   --apply, so a dry run cannot write even if asked to. The post-write mtime read is a Step 8 read-only `stat`.
8. --apply per sidecar, in order: pull (fresh) -> plan -> keep the original locally
   (<GPHOTOS_STATE_DIR>/mvmeta/<UTC ts>/originals/NNNN.mvmeta.json + backup.jsonl, fsynced) -> push `<name>.tmp`
   whose local mtime is the original's (adb push keeps it — RESEARCH F3; a sidecar's mtime is its push time in the
   device evidence) -> `mv` over the sidecar -> pull back, bytes must equal -> stat: mtime kept (a changed mtime is a
   warning). The FIRST failure stops the run (the rest "not reached"); a re-run continues (upgraded = up to date).
   A sidecar with no listed mtime is left as it is (its push time could not be kept).
9. Dry run (default) writes NOTHING anywhere: pulls go to a temporary folder removed at the end; no run directory, no
   library write (the library is never written by this command at all), no device write.
10. Disabled mode kept: MVMETA_UPGRADE_APPLY_ENABLED = True (G0 q2); False -> --apply prints one line containing
    `device rewrites disabled (OD-4 clarification)` and returns status disabled (exit 0) — no library read, no adb.

## Assumptions (also listed in the final report)
- (A1) Every sidecar on the phones was written by write_remote_mvmeta (unchanged since 8c12680, 2026-05-30, until
  Step 5's v2): json.dump(indent=2), text mode on Windows -> CRLF — confirmed for the REAL writer's output on this
  machine (13c.a, 13c.d); on the real phones' files at the first live dry run (A7).
- (A2) adb push keeps the local file's mtime and the device `mv` keeps it (RESEARCH F3, verified live 2026-09-24);
  the post-write stat checks it on the user's phones at R3/§M17.
- (A3) A plain `mv` over an existing file is an atomic rename on the phone's /sdcard (cmd_push relies on the same
  rename for every chunk).
- (A4) §1d's "check-only" for HT74D0202839 means: never rewritten by default — only when the user names it with
  `device` (design decision 6). If the user wants the extra phone included by default, it is a one-line change in
  `_mu_phones`.
- (A5) OD-4's "you can change all existing library also" covers whole-file sidecars and every cloud-bearing status
  (onboarded / archived / restored_local), not only "archived split entries" (deviation D1).
- (A6) A sidecar already at v2 (a push since Step 5) may gain the keys it lacks (e.g. gphotos_account once confirmed);
  its version stays 2 and nothing it holds changes.
- (A7) The live dry run was not possible (no phone attached at the gate), so A1 (every real sidecar in the writer's
  exact form) is unverified on real data: the first live dry run (R3 / §M17) shows it — a sidecar not in that form is
  listed "left as it is", never rewritten.

## Log
- 2026-10-14 dispatched; read-in: base contract (executor-opus.md), PLAN (journal, P-1…P-20, OD table, protocol,
  Steps 5 / 8 / 13 / 13b / 13c / 15 / 16, consumer rows, risks, §M17), DECISIONS (§1, §1b, §1c, §1d, §1e, §1f),
  PROGRESS, root STATUS.md Steps 5 / 6 / 8 / 9, `.candidates/c25-step13{,b}/STATUS.md`, main.py (write_remote_mvmeta,
  push_one_extra, cmd_push's sidecar site, the device section, the apply engine fold / home rules, map_photos scope +
  devices-only, check_photos' taken-time rule, the auto-confirm section, the dispatcher), mvcommon's identity section
  (block, iter objects, read_capture, learned store, apply_learned), gpcapture.py, gpweb dedupKey helpers,
  tests/conftest.py, docs/testing-strategy.md, tests/test_device_inventory.py, tests/test_gp_capture.py, the
  dispatcher harnesses. git history: write_remote_mvmeta's v1 body is unchanged since 8c12680.
- Model: session env claude-fable-5-1; the harness's commit-attribution notice alternates Fable 5.1 / Opus 5.5 (the
  quirk Steps 8–13b recorded) — each commit carries the notice current at commit time.
- 2026-10-14 a usage-limit pause hit during 13c.c (main.py's cmd_mvmeta_upgrade + parser + helpers written, +332
  lines, uncommitted, no tests yet); resumed on the coordinator's word: the diff was coherent (it parses, imports,
  additive only) -> kept; two refinements made on review before its tests: a row interrupted mid-write is marked
  failed (it would have printed as "would upgrade"), and report.txt holds the whole transcript (progress lines too).
- 2026-10-14 13c.c: one environmental outlier (a 7 s fixture setup, not reproducible alone: 0.27 s); the dispatcher
  harness now compiles main.py's guard once per module (~2 s a test before).
- 2026-10-14 13c.e: `adb devices` listed no phone at the gate (twice) -> no live dry run; nothing was run against a
  device. The commit trailers of this step all carry the notice current at each commit (Opus 5.5 every time; the
  notice named Fable 5.1 once mid-step, between commits).

## Step 13c — [status: done]
- Executor: executor-fable
- Model: fable (session env claude-fable-5-1; each commit's trailer is the harness attribution notice current at commit
  time — Opus 5.5 at every commit of this step, the notice naming Fable 5.1 once in between: the quirk Steps 8–13b
  recorded; one usage-limit pause, resumed on the coordinator's word). No MODEL-FALLBACK banner in the dispatch.
- Mode: single-executor in its own worktree (C:\Users\harin\PycharmProjects\MediaVault-s13c, branch tmp/c25_step13c),
  pathspec WIP commits per the PLAN's in-step checkpoint protocol (the dispatch's instruction).
- Files changed: main.py (a new section "GOOGLE PHOTOS IDENTITY — mvmeta_upgrade" after the Step 13b auto-confirm
  section — MVMETA_UPGRADE_APPLY_ENABLED / _DISABLED / _VERSION / _KEYS / _USAGE, MVMETA_TMP_SUFFIX,
  MVMETA_ADB_TIMEOUT_S, _mu_owners, _mu_taken_utc, _mu_facts, _mu_encode / _mu_decode, _mu_same, _mu_upgrade,
  _mu_only_added, _MvmetaCommandRefused, _mu_sidecar_path, _mu_local, _mu_argv_ok, _mu_run, _mu_adb_problem,
  _mu_pull, _mu_mtime, _mu_new_run_dir, _mu_keep_original, _mu_write, parse_mvmeta_upgrade_args, _mu_phones,
  _mu_locate, _mu_row, _mu_plan, the report helpers, cmd_mvmeta_upgrade — plus one usage line and the dispatcher
  branch; purely additive: no existing line changed), tests/test_mvmeta_upgrade.py (new, 162 tests),
  .candidates/c25-step13c/STATUS.md (this file). NOT changed: Step 8's device section (_dev_argv_readonly and its
  pins), write_remote_mvmeta, gpcapture, mvcommon, mainfetch, the apply engine, the rollback machinery,
  ENTRY_TYPE_KEYS, conftest.py, any existing test.
- Outcome: `python main.py mvmeta_upgrade [all|<id>|<prefix>] [device <alias|serial>] [--apply]` brings the phones'
  `.mvmeta.json` sidecars of the existing library to v2. It lists the phones read-only (Step 8's device_inventory),
  finds each uploaded entry's / extras item's sidecar by name (Step 8's classify_device_objects), and for those whose
  objects have PROVEN facts pulls the sidecar into a temporary folder: per chunk it would add sha1 + dedup_key (only
  from a capture, the trusted block, or a dedupkey / sha256 record — never a name match), date_utc (the file's own
  DateUTC, else a confirmed record's taken time), uploaded_name (block, else capture) and gphotos_account (the
  confirmed copy's account), appended after the chunk's own keys; a v1 sidecar that gains a key becomes version 2.
  A sidecar of other bytes, of another item, in an unknown version, not in the writer's exact bytes, with a value
  that disagrees, an unknown push time, or a name two items claim is left as it is (listed). The dry run (default)
  prints per sidecar the keys --apply would add and writes nothing anywhere. --apply rewrites one sidecar at a time:
  original kept locally (~/.mediavault/gphotos/mvmeta/<UTC ts>/originals + backup.jsonl, fsynced) -> push
  `<sidecar>.tmp` carrying the original mtime -> `mv` over the sidecar -> pull back, byte-equal -> stat, mtime kept
  (a warning if not); the first failure stops the run, a re-run continues; report.txt = the transcript. Without
  `device`, every device-check phone is read but only the push phones (DEVICE_ALIASES) are rewritten — the
  check-only HT74D0202839 only when named. Offline / unauthorized / unplugged / placeholder phones get one line.
  Exit 1 for an unknown scope, a failed write, Ctrl+C or a usage error; 0 otherwise (also for the kept disabled mode).
- Key decisions: design decisions 1–10 and assumptions A1–A7 above. Deviations from the PLAN text, each surfaced:
  (D1) every uploaded entry / extras item whose sidecar is found — whole files and every cloud-bearing status — not
  only "archived split entries" (OD-4: "you can change all existing library also"; Step 5 writes the same v2 keys
  for whole files; the series phone's sidecars are mostly whole episodes) · (D2) scope `all|<id>|<prefix>`
  (map_photos' rule: a season id selects its episodes) where the PLAN writes `[all|<id>]` · (D3) `device` takes a
  device-check alias (e.g. movies#2) or a serial; a check-only phone is rewritten only when named (§1d) · (D4) the
  originals are kept locally before any rewrite (+ report.txt) — not in the PLAN; an irreversible phone write gets a
  local undo · (D5) the original mtime is carried over and checked after the write — the device check reads a
  sidecar's mtime as its push time · (D6) a v2 sidecar (a push since Step 5) also gains the keys it lacks ·
  (D7) gphotos_account is per chunk (the PLAN gives no place) · (D8) uploaded_name only from a recorded fact, never
  derived (a legacy sidecar's chunk `filename` already is the pushed name) · (D9) the disabled line carries the
  PLAN's phrase inside a self-explanatory sentence · (D10) items without a sidecar: ids listed up to 20, else counted
  per category · (D11) only the writer's exact bytes are rewritten (anything else is left as it is) · (D12) a sidecar
  with nothing proven is not even pulled · (D13) "every adb argv carries -s <serial>": all but the one host query
  `adb devices` of Step 8's listing, which addresses no phone (pinned exactly that way).
  Contracts for later steps — 15: cmd_mvmeta_upgrade(scope="all", device=None, apply=False) -> {status, apply, scope,
  rows, missing, devices, run_dir, counts}; a smoke dry run needs a fake adb answering devices / find / stat / pull —
  tests/test_mvmeta_upgrade.py's directory-backed Phone + FakeAdb (and its round trip over mock_device's
  sdcard/Media) show how; pins worth taking: MVMETA_UPGRADE_KEYS, MVMETA_UPGRADE_VERSION == MVMETA_SCHEMA_VERSION,
  the _mu_argv_ok vocabulary, "_dev_argv_readonly not widened"; the "mvmeta sha1 only when proven" mutation is P1
  (_MU_CONTENT_PROOFS + "filename"). 16 (docs): what changes on the phone (the five per-chunk keys, only proven ones;
  every v1 byte kept, version 1 -> 2; the mtime kept; the originals folder and how to put one back; the check-only
  phone needs `device <alias>`; dry run first). R3 / §M17: run it after map_photos / check_photos --apply — before
  them most legacy sidecars read "nothing proven yet"; the first live dry run also confirms A1 / A2 on real phones.
- Verification: tests/test_mvmeta_upgrade.py 162 passed (≈13–30 s); neighbours per sub-task (see 13c.a–d); disarm
  38/38 caught (scratchpad disarm_13c.py on a `git archive` copy of 6e06df5, the copy green before and after);
  full suite 3062 passed (2900 + 162), 1 pre-existing warning, 458 s; smoke 82 passed (20 s), run after it; live dry
  run not run (no phone attached). Self-review (V2 delta 4): (a) the Step 13c bullets one by one — files, the CLI, the
  details (Step 8 listing, pull to a temp folder, v2 = v1 unchanged + the proven per-chunk keys, the dry run prints
  per entry the keys, --apply = push .tmp -> mv -> re-pull + compare, never a media or other file, offline phone ->
  a line, no-sidecar entries listed only, the disabled path kept) and the test list (fake adb recording pull / push
  / mv, every argv pinned to -s, v1 keys byte-identical, unproven sha1 never written, tmp + mv ordering, offline
  phone, disabled mode, the disarm probe) — all covered, deviations above; (b) consumers R3 / R4 §M17, Step 15, Step
  16 — contracts above; (c) change-gates — no rollback code or journal touched (no such symbol in the diff; 0 removed
  lines in main.py), no library write at all, ENTRY_TYPE_KEYS untouched (guard green), the whole-library pass is
  mvcommon.iter_uploaded_objects (alias / season_map safe — an alias resolves to its primary, a season gives its
  extras), smoke green. The step was correctly routed as complex (device writes behind byte-identity and proven-facts
  rules) — no misroute.
