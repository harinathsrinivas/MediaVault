STATE: IN PROGRESS — next: 13c.a (the pure core: proven facts, sidecar codec, upgrade builder, byte-identity check)

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
- [ ] 13c.0 this status file: the sub-task plan + the design decisions below (no code)
- [ ] 13c.a main: the PURE core — `_mu_facts(obj)` (the proven v2 facts of one uploaded object), `_mu_decode` /
      `_mu_encode` (the sidecar's exact bytes: json.dump indent=2, ASCII, the file's own line ends), `_mu_upgrade`
      (validate + append the missing proven keys) and the byte-identity check — tests: facts rules (sha1 never from
      filename / manual proof, untrusted block ignored, date_utc ladder, account rule, conflicts), v1 keys
      byte-identical, version handling, canonical-form refusals, present-value conflicts
- [ ] 13c.b main: the device path — `_mu_argv_ok` (its OWN allowlist: pull; with --apply push `<sidecar>.tmp` + `shell
      mv` tmp -> sidecar), `_mu_run`, `_mu_pull`, `_mu_write` (keep the original locally -> push .tmp with the
      original mtime -> mv -> pull back + compare -> stat the mtime) — tests with a directory-backed fake adb:
      ordering, -s pins, refusals, a dry run cannot write, failure at every stage, the original kept, the mtime kept,
      `_dev_argv_readonly` NOT widened
- [ ] 13c.c main: `cmd_mvmeta_upgrade(scope, device, apply)` (scope like map_photos, phones, owners from
      iter_uploaded_objects, sidecars located by Step 8's classify_device_objects, plan / write loop, report, the
      disabled mode, Ctrl+C) + `parse_mvmeta_upgrade_args` + the CLI branch + the usage line — tests end to end: the
      dry run writes nothing anywhere, --apply, offline / unauthorized / not-connected / placeholder phones, the
      check-only phone, scopes (id, alias, season prefix, extras, unknown), the no-sidecar list, idempotent re-run,
      the dispatcher
- [ ] 13c.d round trip through the REAL writers: v1 sidecars from write_remote_mvmeta at version 1, v2 from today's
      push (cmd_push / push_title_extras on the mock device), a confirmation through the real apply engine, then
      mvmeta_upgrade over the same phone — all four prefixes, chunks + FLAC holder, extras whole + split, an alias
- [ ] 13c.e gates: disarm probes (isolated scratch copy) · full suite · smoke · live read-only dry run (only if
      `adb devices` lists a phone; counts only) · self-review · STATE: COMPLETE · final feat commit

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
  Step 5's v2): json.dump(indent=2), text mode on Windows -> CRLF. Validated on the real sidecars by the live dry run.
- (A2) adb push keeps the local file's mtime and the device `mv` keeps it (RESEARCH F3, verified live 2026-09-24);
  the post-write stat checks it on the user's phones at R3/§M17.
- (A3) A plain `mv` over an existing file is an atomic rename on the phone's /sdcard (cmd_push relies on the same
  rename for every chunk).

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
