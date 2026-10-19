STATE: IN PROGRESS — next: 15.d2 the alias sweep over the new passes (then 15.d3 all four prefixes)

# IMP-C25 Step 15 — smoke coverage, regression pins, mutation probes — resumable status

- Plan: docs/feature-fetch-datetime/PLAN.md Step 15 (+ P-2, P-4, P-5, P-9…P-13, P-17, P-19, P-20; Steps 9b, 11, 13b,
  13c, 14) · contracts from the root STATUS.md of the main folder (Steps 2–11, read only) and
  .candidates/c25-step-12/C/*, c25-step13/, c25-step13b/, c25-step13c/, c25-step14/ STATUS.md.
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-s15` · branch `tmp/c25_step15` · base 068212e (Steps 0–14 done).
  The main folder is never touched (the user archives from it).
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe -m pytest …`, run from the worktree.
  Gates run SEQUENTIALLY. Baseline (orchestrator, 5b21c3f): full suite 3219 · smoke 82.

## Resume rule
Read this file, then `git log --oneline -15` (WIP commits: `wip(imp-c25): step 15.<x> — …`) and `git status`. An
uncommitted diff belongs to the first unticked sub-task: keep it when coherent and its targeted tests pass, else
`git checkout -- <its files>` (record that here). Continue at the first unticked sub-task. Commit THIS file
(force-added: `.candidates/` is gitignored) with every WIP commit; tick a sub-task only after its tests pass, with its
SHA. Pathspec commits only; never -A, amend, rebase or stash. Never edit PROGRESS.md (the orchestrator does).

## Sub-tasks (one WIP commit each)
- [x] 15.0 this status file: the sub-task plan, the design decisions, the draft guard map (no code) — f35cc56
- [x] 15.a smoke I (offline, tests/smoke/test_smoke_all_commands.py): prep + push write the capture (prep record, one
      complete push record, the library gains no `gphotos` block — P-11) · `check_photos pending --list` lists the
      unverified push offline (no Chrome, no child) · fetch + apply merge: a seeded learned sidecar is folded into the
      library by cmd_dispatch_fetch's post-child merge (mock_device's neutralized child) — the block lands, pinned.
      Done: Group 5 header + helpers (`_c25_tree` file/folder snapshot, `_c25_no_chrome`, `_c25_prep_push`) and
      class TestC25ExactItem: test_prep_and_push_capture_the_facts_and_never_confirm ·
      test_check_photos_pending_list_is_offline (through parse_check_photos_args; tmp tree unchanged) ·
      test_fetch_merges_a_seeded_learned_sidecar_into_the_library (the merge line after the child's output; block
      pinned + confirmed; a second fetch folds nothing). `import mainfetch` added to the smoke file's imports.
      Tests: -k TestC25 3 passed (0.98 s); smoke 85 passed (19.8–21.9 s; the real-split fixture's setup is 6.9 s of
      it) — 00f6ec9
- [x] 15.b smoke II (the fake Google Photos world, in-process): `check_photos <season> --approaches best` with an
      in-process runner for main._cp_spawn (dry run: only its run dir written, nothing downloaded) · `map_photos` dry
      run over a synthetic fresh inventory (only its run dir written; library bytes unchanged) · a fake fetch in incoming
      mode: the entry's `restore\.mv_incoming` is gone afterwards, the file is in `restore\`, the redirected
      ~/Downloads is byte-for-byte untouched (a pre-existing file there survives).
      Done: helpers `_c25_wire` (fake clock for mainfetch + gprpc, fake driver per session, lock / _check_lock /
      port probe / jitter / disk stubbed, S4 recorder, main._cp_spawn = the real `mainfetch.py check` command line
      in-process), `_c25_season` (EP1_ID trusted block → S0a / fast path; EP2_ID legacy → S2), `_c25_inventory` (the
      crawler's files, a complete enumeration begun an hour ago), `_c25_no_adb`, `_c25_bytes`, `_C25Clock`,
      `_C25Disk`; imports contextlib / types / datetime / gprpc / gpweb. Tests:
      test_check_photos_best_on_the_fake_world_writes_only_its_run (one in-process child; e01 dedupkey + e02 filename;
      no download, no pLFTfd, only allowlisted rpcids; library bytes + sidecars unchanged; only mv_state written) ·
      test_map_photos_dry_run_maps_and_writes_only_its_run_directory (mapped; no adb; only its run dir) ·
      test_a_fetch_leaves_no_incoming_folder_and_never_touches_downloads (the direct download landed IN
      restore\.mv_incoming, the file is in restore\ alone, the folder is gone, the redirected ~/Downloads with a
      same-named and an unrelated file byte-identical, journal clean + entry mode incoming / cleaned). -k TestC25 6
      passed (0.96 s, each ≤ 0.17 s); smoke 88 passed (12.9 s) — 558a952
- [x] 15.c smoke III: `mvmeta_upgrade` dry run on the mock device (devices / find / stat / pull answered from
      mock_device's tree; writes nothing anywhere) · `resolve_photos` non-TTY refusal (exit status refused, nothing
      launched, nothing recorded) · `fetch_cleanup` dry run lists a leftover and writes nothing (tree snapshot).
      Done: `_C25ReadOnlyAdb` (devices · shell find · shell stat · pull over one phone folder; push / mv / anything
      else fails the smoke) · test_mvmeta_upgrade_dry_run_on_the_mock_device_writes_nothing (prep + a version-1 push
      on mock_device — the pre-Step-5 writer, as the focused round trip does — then the dry run proposes the v1 → v2
      upgrade; read verbs only; tempfile.tempdir under tmp_path; the whole tmp tree unchanged) ·
      test_resolve_photos_refuses_without_a_terminal (a seeded failed row; stdin = io.StringIO, explicitly not a
      TTY even under -s; init_driver AND mainfetch._check_lock forbidden — a regression past the TTY check must never
      take the real fetch lock; status refused, nothing written) ·
      test_fetch_cleanup_dry_run_lists_the_leftovers_and_writes_nothing (family name + its .crdownload in the
      redirected ~/Downloads, an unrelated file never listed, a marked .mv_incoming leftover listed, the marker not;
      every listed / scanned path under tmp_path — FETCH_SESSION_LOCK pointed at tmp too; nothing written). Imports
      io / shlex / shutil / sys / tempfile. -k TestC25 9 passed (1.18 s); smoke 91 passed (13.5 s) — bac0f1b
- 15.d smoke IV — split into three WIP commits (usage limits hit every few hours):
  - [x] 15.d1 push-family dispatcher runs (push, push_group, prep_push_rep, prep_push_rep_season,
        prep_push_rep_enrich, prep_push_rep_season_enrich) reach the hook recorder and never spawn the auto-confirm
        child. Done: `_c25_dispatcher` (main.py's `if __name__ == "__main__":` block is the file's tail — compiled
        ALONE with main.py's line numbers: a whole-file ast.parse costs ~3 s of the gate), `_C25Argv`, `_c25_main`,
        `_c25_push_family_case`; class TestC25PushFamilyHook::test_a_push_family_run_reaches_the_hook_and_spawns_no_child
        [6 verbs] — the REAL commands (mock_device / fake_dummy / stub_tech_specs / mock_tmdb + the by-id details,
        _forbid_input), each run reaches its end state, `main.after_push_command.calls` == [(the exact hook args, {})],
        maybe_auto_confirm never called, main._cp_spawn AND main.subprocess.Popen record + refuse: nothing spawned.
        -k TestC25PushFamilyHook 6 passed (1.67 s, each ≤ 0.23 s) — SHA in the 15.d2 commit
  - [ ] 15.d2 the alias sweep over the new whole-library passes: iter_entry_objects / iter_uploaded_objects /
        unverified_objects / pending --list / fetch_cleanup's family (offline) · check_photos with the ALIAS scope on
        the fake world · map_photos with the alias scope + the apply engine fed a record naming the alias id · mvmeta_
        upgrade with the alias scope. (resolve_photos is left out of the sweep: its first act is the TTY refusal —
        15.c — and its `--id <alias>` row selection is pinned in test_resolve_photos.)
  - [ ] 15.d3 all four prefixes (mov- tv- ani- oth-): real prep + push of one object each → pending --list (four
        accounts) → map_photos dry run (four mapped by their own bytes) → the merge of four learned records (four
        blocks, each in its own library file, confirmed) → nothing pending
- [ ] 15.e pins: tests/test_c25_regression_pins.py — a registry that the existing pins still exist (referenced, never
      duplicated) + only the missing pins (candidates: the exact spawned fetch argv · the P-13 marker literals on both
      sides · calculate_file_hash's return contract · the mvmeta v1 key order) — each checked against what exists first
- [ ] 15.f disarm: one consolidated probe script (session scratchpad, outside the repo) run on an isolated `git archive`
      copy of the final code — every guard of the PLAN's list has ≥ 1 probe re-run there; a test is added only where a
      guard turns out uncovered; `.candidates/c25-step15/DISARM.md` = guard → test → probe → caught, `disarm N/N`
- [ ] 15.g gates: full suite (baseline 3219 + this step's) and smoke (82 + this step's) sequential, smoke runtime
      recorded · self-review · STATE: COMPLETE · final commit
      `test(imp-c25): Step 15 — smoke coverage, regression pins, mutation probes for every new guard`

## Design decisions (binding for the rest of this step — recorded early so a resumed session stays consistent)
1. Smoke additions go in ONE new group at the end of tests/smoke/test_smoke_all_commands.py ("Group 5 — IMP-C25"),
   driven through the existing fixtures (sandbox, sandbox_alias, mock_device, fake_photos, make_video,
   stub_tech_specs, fake_dummy, mock_tmdb) plus small self-contained helpers in the smoke file. No import from focused
   test modules (a refactor of their private helpers must never break the pre-PR gate); tests/conftest.py untouched
   unless a smoke cannot be written without it (then: say why here).
2. Smoke stays "no crash + correct top-level effect": every new smoke asserts the command's own effect plus the one
   safety property the PLAN names for it (writes nothing / nothing launched / nothing spawned / folder gone).
   Budget: < 30 s for the whole package on an idle machine; the runtime is recorded at 15.g.
3. Existing tests: zero edits. Change-gates: no rollback symbol, no ENTRY_TYPE_KEYS, no product code is changed by this
   step (tests + this folder only); if a probe exposes a real gap, the fix is surfaced to the orchestrator first.
4. Pins: tests/test_c25_regression_pins.py does NOT re-assert what an existing pin asserts — it holds a registry
   (module + test name) of the existing pins, so a deleted or renamed pin fails here, and adds only the pins that are
   missing.
5. Disarm: each probe = one exact, single-occurrence source replacement on the scratch copy (the earlier steps'
   harness, scratchpad disarm_all.py), the named catcher files run with -x; caught = at least one test fails; every
   mutated file is restored and compared byte for byte; the unmutated copy must be green first.
6. Two guards overlap in the PLAN's list; they are split by WHAT is protected, so each gets its own probes:
   "never-delete-foreign" = a file MediaVault did not cause to exist is never deleted (a pre-existing or non-family
   file in ~/Downloads in the fallback and start-up cleanups, a file that changed between fetch_cleanup's listing and
   its --apply, a phone file other than the sidecar mvmeta_upgrade rewrites); "deletions only inside `.mv_incoming` /
   run-new expected-family files" = the delete-scope predicate itself (`_is_incoming_dir`: folder name + marker, never
   a sub-folder; the expected-name family rule; pending deletes only where the rule allows).

## Draft guard map (from the step reports — every row re-verified against the current code at 15.f)
| # | Guard | Code | Catching tests (draft) | Earlier probes |
|---|---|---|---|---|
| 1 | pin rule (P-2) | mvcommon.block_trusted; engine replace of untrusted blocks | test_gphotos_primitives (block_trusted table, cloud_state), test_gphotos_apply, test_entry_schema_guard (d3) | Step 3 D1 / D1b; Step 6 D2 |
| 2 | verification-before-trigger (P-5, Goal 2) | mainfetch locator verdicts; `_run_locator_phase` requests only located items; `_shows_item` before Shift+D | test_locator (same-second batch, wrong-click case, nothing verifiable, neighbour page) | Step 12 C P1, P2, P8, P25 |
| 3 | dry-run-writes-nothing | gphotos_apply(dry_run) · map_photos · check_photos · fetch_cleanup · mvmeta_upgrade | test_gphotos_apply, test_map_photos, test_check_photos, test_fetch_hygiene, test_mvmeta_upgrade | Step 6 D1/D20; Step 9 dry-run probes; Step 11 "dry run removes folders"; 13c P14/P15 |
| 4 | never-delete-foreign | fallback harvest + start-up cleanup (run-new snapshot, family), fetch_cleanup --apply re-check | test_fetch_hygiene | Step 11 "fallback deletes pre-existing files", "non-family files", "start-up ignores the snapshot", "--apply skips the re-check" |
| 5 | home-account routing (P-4) | mainfetch._object_account / _route_entry / _account_groups | test_fetch_routing, test_locator (wrong-account) | Step 10 "learned layer removed", "learned outranks override", "single-session shortcut removed" |
| 6 | freshness gate (24 h, routed account, Step 9b recency) | main._mp_freshness / _mp_recency; check_photos _cp_recent | test_map_photos, test_check_photos | Step 9 "freshness gate / 24 h limit / newest-push rule removed"; Step 9b probes |
| 7 | no adb without `--devices` | map_photos / check_photos device check opt-in | test_map_photos, test_check_photos | Step 9 "adb run needlessly"; Step 9b |
| 8 | capture-never-confirms (P-11) | mvcommon cloud_state / confirmed rule; engine capture fold | test_gphotos_primitives, test_gphotos_apply | Step 3 D4; Step 6 D3 / D3b |
| 9 | RPC allowlist (P-9) | gprpc.call outer check + send-site check | test_gprpc, the after-checks of test_locator / test_check_photos | Step 2b D1–D3; picks P43 / P44 |
| 10 | apply fingerprint re-check (P-10, OD-2) | main.gphotos_apply fingerprint before save | test_gphotos_apply | Step 6 D4 / D27 / D28 |
| 11 | auto-confirm only after success (P-19) | main.after_push_command + the six dispatcher call sites | test_auto_confirm | 13b P1 / P2 / P3 / P22 |
| 12 | mvmeta sha1 only when proven | main._mu_facts / _MU_CONTENT_PROOFS | test_mvmeta_upgrade | 13c P1 / P2 |
| 13 | download URL never persisted or printed (P-20) | gprpc DownloadUrl wrapper; journal whitelist; locate --probe-direct | test_gprpc, test_fetch_hygiene, test_locator (scan) | Step 2b D4 / D14; Step 11 "journal keeps a URL"; Step 12 C P14 |
| 14 | deletions only inside `.mv_incoming` / run-new expected-family files (P-12) | mainfetch._is_incoming_dir, _family_bases, the fallback rule, pending deletes | test_fetch_hygiene | Step 11 "marker ignored", "folder name ignored", "family widened", "pending deleted anywhere", "unmarked folders listed" |
| 15 | Chrome kill only for the journaled pid with a matching user-data-dir | mainfetch._chrome_still_ours / the start-up cleanup / _terminate_session_chrome | test_fetch_hygiene, test_chrome_robustness | Step 11 "Chrome judged ours without its command line", "start-up terminates an attached Chrome"; 14.a-2 |

## Assumptions (also listed in the final report)
- (A1) The commit trailer names the model this session runs on: Claude Opus 5.5 (claude-opus-5-5).

## Log
- 2026-10-19 dispatched; read-in: PLAN (P-1…P-20, OD rulings, protocol, Steps 9–18, consumer rows, risks), PROGRESS,
  root STATUS.md Steps 2–11 + the live wrong-item pick (main folder, read only), .candidates/c25-step-12/C/
  {CANDIDATE,PICKS}_STATUS.md, c25-step13{,b,c}/ and c25-step14/ STATUS.md, docs/testing-strategy.md, the smoke file
  and its conftest, tests/conftest.py, gphotos_fake's contract, the check/fetch harnesses of test_check_photos /
  test_locator, the existing pins (test_cli_parsers, test_fetch_routing, test_fetch_search_wait, test_gprpc,
  test_entry_schema_guard, test_gp_capture), the scratchpad probe harness disarm_all.py.
- 2026-10-20 a usage-limit pause hit during the read-in (nothing on disk yet); resumed on the coordinator's word —
  this file first.
