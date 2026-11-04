STATE: COMPLETE

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
        -k TestC25PushFamilyHook 6 passed (1.67 s, each ≤ 0.23 s) — ae2871a
  - [x] 15.d2 the alias sweep over the new whole-library passes: iter_entry_objects / iter_uploaded_objects /
        unverified_objects / pending --list / fetch_cleanup's family (offline) · check_photos with the ALIAS scope on
        the fake world · map_photos with the alias scope + the apply engine fed a record naming the alias id · mvmeta_
        upgrade with the alias scope. (resolve_photos is left out of the sweep: its first act is the TTY refusal —
        15.c — and its `--id <alias>` row selection is pinned in test_resolve_photos.)
        Done: class TestC25AliasSweep (the primary pushed THROUGH the alias id on mock_device, its push capture) —
        test_the_new_iterators_skip_the_alias (alias yields no object; uploaded / unverified / failed_objects /
        pending --list = the primary alone; fetch_cleanup's family walk; the alias entry byte-equal) ·
        test_check_photos_resolves_an_alias_scope_to_its_primary (fake world, in-process child: one line, the
        primary, exactly-one, the item's photo id) · test_map_photos_and_the_apply_engine_resolve_the_alias (a LEGACY
        archived primary — no capture — mapped by exact name over the alias scope; a learned record naming the ALIAS
        id lands on the primary, confirmed; the alias untouched; no adb) ·
        test_mvmeta_upgrade_resolves_an_alias_scope_to_its_primary (the row is the primary's own sidecar; the temp
        folder empty). Finding while writing it (not a defect — the Step 9b rule working): a push via the alias of an
        un-prepped primary records no SHA-1, so map_photos rates that fresh push `not-backed-up-yet` (only its own
        bytes could prove a push newer than the crawl) — hence the legacy object in the map test. `_c25_inventory`
        takes (name, dedupKey) pairs and returns the item records. -k TestC25AliasSweep 4 passed (1.23 s); smoke 101
        passed (12.6 s) — 4a20ea3
  - [x] 15.d3 all four prefixes (mov- tv- ani- oth-): real prep + push of one object each → pending --list (four
        accounts) → map_photos dry run (four mapped by their own bytes) → the merge of four learned records (four
        blocks, each in its own library file, confirmed) → nothing pending.
        Done: C25_FOUR + class TestC25FourPrefixes::test_every_prefix_goes_from_push_to_confirmed (each routed to
        its own account — movies / tv / anime / others; each mapped with proof dedupkey; the merge: saved, 4 changed,
        4 newly confirmed; each block read back from ITS library file — library_movies / _series / _anime /
        _others.json; pending empty afterwards; no adb). -k TestC25FourPrefixes 1 passed (0.64 s); smoke 102 passed
        (14.9 s) — d6f95ca
- [x] 15.e pins: tests/test_c25_regression_pins.py — a registry that the existing pins still exist (referenced, never
      duplicated) + only the missing pins (candidates: the exact spawned fetch argv · the P-13 marker literals on both
      sides · calculate_file_hash's return contract · the mvmeta v1 key order) — each checked against what exists first.
      Done (39 tests, 3.7 s — main.py's ast.parse is ~3 s of it): (1) test_the_pin_still_exists × 24 — the registry
      of the EXISTING pins by file + name (a method must sit in its class): parse_fetch_args (TestParseFetchArgs ×4,
      the 4-tuple with --manual, parse_fetch_options) · spawned argv (argv + env + kwargs, the child-env structural
      pin, --manual only when given ×2, the web UI never passes it, the check child, the auto-confirm child) · web
      markers (today's transcripts' counts) · trigger_download + search-wait digests · calculate_file_hash ×3 ·
      ENTRY_TYPE_KEYS (d1) · mvmeta v1 ×3 · ALLOWED_RPCIDS. (2) the MISSING pins:
      test_fetch_restore_spawns_exactly_the_fetch_child_argv × 4 (bare / episodes / extras + tempdir / manual — the
      fetch_restore path, i.e. the web UI's action, was unpinned) · test_the_p13_markers_are_printed_only_where_they_
      always_were × 6 (the exact source of every marker-bearing string literal, docstrings excluded: mainfetch 4 ·
      main.py 2 (cmd_restore's split lines) · gprpc / gpweb / mvcommon / gpcapture none) ·
      test_the_web_progress_parser_scans_exactly_these_markers (the three regex patterns + re.M + _PROGRESS_SIGNALS) ·
      test_calculate_file_hash_signature_and_return_type (filepath, block_size=65536; a lowercase 64-hex str;
      main's name is the same function) · test_calculate_file_hash_returns_none_and_never_raises_when_the_file_cannot_
      be_read (a directory: None + the ❌ line, no cached SHA-1) · test_the_mvmeta_writer_keeps_the_v1_key_order_and_
      its_exact_byte_form × 2 (whole / split: the 14 v1 keys in writer order, chunks [filename, hash], ASCII,
      main._mu_decode accepts the bytes — mvmeta_upgrade's byte-identity precondition) — ef29600
- [x] 15.f disarm: one consolidated probe script (session scratchpad, outside the repo) run on an isolated `git archive`
      copy of the final code — every guard of the PLAN's list has ≥ 1 probe re-run there; a test is added only where a
      guard turns out uncovered; `.candidates/c25-step15/DISARM.md` = guard → test → probe → caught, `disarm N/N`
  - [x] 15.f0 (found while designing the probes) the smoke's adb stand-ins RECORD every call before refusing it —
        device_inventory swallows errors (test_map_photos says so: "the list is the pin"), so a raising stand-in
        alone could be absorbed: `_c25_no_adb` → `_c25_record_adb(monkeypatch)` (returns the list; the three
        map_photos smokes assert it empty) · `_C25ReadOnlyAdb` records the verb of EVERY call first. Smoke 102
        passed (14.5 s) — e94f495
  - [x] 15.f1 the probe script `<scratchpad>\disarm_15.py` (check mode: every target occurs exactly once; run mode:
        `git archive HEAD` → scratch copy, a copy-only adb safety net appended to ITS tests/conftest.py — a probe
        that switches the device check on must never reach the real adb — baseline of every catcher file green,
        each probe: edits → catchers → restore + sha256 check; results.jsonl, resumable) and its run.
        Written: C:\Users\harin\AppData\Local\Temp\claude\C--Users-harin-PycharmProjects-MediaVault\63b4ae7b-212c-4a85-
        8e4a-1436806bc271\scratchpad\disarm_15.py — 62 probes (G1–G15: 55 · S: 7); catchers run WITHOUT -x so every
        failing test is listed; a timeout counts as caught (flagged). `check` on the worktree at e94f495: 62 probes,
        0 problems (every target exactly once, every catcher file / test present). Run command (resumable — re-run the
        same; results.jsonl keeps the finished probes): `python disarm_15.py run C:/Users/harin/PycharmProjects/
        MediaVault-s15 <scratchpad>/disarm_15_copy <scratchpad>/disarm_15_results.jsonl`
        RUN (2026-10-20, one uninterrupted run on a fresh `git archive` copy of fc4be59 — it completed, exit 0; a
        coordinator note that it had died at 22/22 was outdated): the probed files byte-equal to the worktree; the
        unmutated copy + adb safety net 1334 passed (112 s); **62/62 CAUGHT** (G1–G15 55/55 · S 7/7), every
        mutated file restored byte for byte; the restored copy 1334 passed (122 s). Results:
        <scratchpad>\disarm_15_results.jsonl + disarm_15_run.log — fc4be59 is the probed code
  - [x] 15.f2 survivors analysed (a test added only for a guard really uncovered, in a NEW file) · DISARM.md
        DISARM.md written: per guard G1–G15 a table (probe · code · mutation · the failing tests, first ones + total ·
        the earlier step's probe of the same guard) + the S table + notes (the G4 / G14 split, the copy's adb net,
        the probes first written here: G5c, G6c, G7b, G9b, G13b — each caught by an existing test)
        No survivor (no test needed). Precision pass (`<scratchpad>\s15_smoke_confirm.py`: the mutation again, ONLY the
        named smoke / focused node): the new smokes catch G3b, G3c, G3e, G8a, G11b by themselves; G7a was caught
        only by test_map_photos (the map smoke's one object was mapped, so no object ever needed a phone) → the map
        smoke now also holds an episode found in no account (possible-loss, still no adb); re-confirmed on the
        copy: it catches G7a and still G3b; the copy's smoke 102 green unmutated — d73e636 (the smoke) · 32f6143
        (DISARM.md)
  Probe design (re-verified against the current code — every target text occurs exactly once):
    G1 pin rule — block_trusted without the pin · without the version · the engine folds an untrusted block
    G2 verification-before-trigger — Step 12 C P1 (any dedupKey verifies) · P2 (any name verifies) · P8 (Shift+D
       without the page check) · P25 (a record anywhere in the page)
    G3 dry-run-writes-nothing — engine dry run saves · map_photos dry run applies · check_photos dry run applies ·
       fetch_cleanup dry run deletes · mvmeta dry run writes (13c P15: all three layers)
    G4 never-delete-foreign — Step 11 P3 (fallback: pre-existing files) · P4 (non-family files) · P6 (start-up:
       snapshot ignored) · P18 (--apply without the re-check) · 13c P33 (mvmeta mv over any phone file)
    G5 home-account routing — fetch: the learned layer removed · learned outranks the override · map/check: the
       learned home ignored (_mp_route)
    G6 freshness gate — the 24 h limit off · a stale default account still decides · recency judged by the default
       (not the routed) account · map recency off · check-child recency off · check verdict recency off
    G7 no adb without --devices — map_photos' device check always on · check_photos' always on
    G8 capture-never-confirms — a capture makes cloud_state confirmed · the engine's capture fold confirms
    G9 RPC allowlist — both checks off · a mutating id allowlisted · the send-site check removed
    G10 apply fingerprint re-check — no re-check · without SHA-256 · taken after the load
    G11 auto-confirm only after success — 13b P1 · P2 · P3 · P22
    G12 mvmeta sha1 only when proven — 13c P1 (filename) · P2 (manual)
    G13 download URL never persisted / printed — a plain-string link · the repr reveals it · the journal keeps the
       trigger's strings (Step 11 P20) · locate --probe-direct's error text (Step 12 C P14)
    G14 deletions only inside .mv_incoming / run-new family — Step 11 P1 (marker) · P2 (folder name) · P5 (family
       widened) · P7 (pending anywhere) · P32 (unmarked folders listed)
    G15 Chrome kill only for the journaled pid + matching user-data-dir — start-up: the command-line check off ·
       session close: another profile accepted (14.a-2) · an attached Chrome terminated at the close (Step 11 P10)
       · the start-up terminates an attached Chrome (Step 11 P11)
    S  Step 15's own additions bite — the post-fetch merge removed · a new P-13 marker line · a read error raises ·
       the mvmeta writer's form changed · fetch_restore drops tempdir · the incoming folder kept · no TTY check
- [x] 15.g gates: full suite (baseline 3219 + this step's) and smoke (82 + this step's) sequential, smoke runtime
      recorded · self-review · STATE: COMPLETE · final commit
      `test(imp-c25): Step 15 — smoke coverage, regression pins, mutation probes for every new guard`.
      On 32f6143 (tree clean), one after the other on an otherwise idle machine: full suite **3278 passed** (= 3219 +
      20 smoke + 39 pins), 1 warning (the pre-existing httpx / Starlette deprecation), 250.6 s, exit 0; then smoke
      **102 passed** (82 + 20), 1 warning, **12.4 s**. Hermeticity: a listing (path, size, mtime) of the real
      ~/.mediavault/gphotos, fetch_journal and locks and of ~/Downloads, taken before the disarm run, is identical after
      the run, the full suite and the smoke (their live 2026-10-18 artefacts are not mine); the real C:\Media\library_
      *.json were last written 2026-10-18 17:36 — before this step began. The scratch copy was removed. Self-review:
      the step report below — the final commit sets STATE: COMPLETE

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
5. Disarm: each probe = exact, single-occurrence source replacement(s) on the scratch copy (the earlier steps'
   harness, scratchpad disarm_all.py), the named catchers run WITHOUT -x (every failing test is listed); caught = at
   least one test fails; every mutated file is restored and compared byte for byte; the unmutated copy must be green
   first and the restored copy green after.
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
- 2026-10-20 further limit pauses during 15.c, 15.e and the disarm run; each resume found the tree clean at the last
  WIP commit (nothing to keep or discard). The coordinator's last note said the disarm run had likely died at 22/22;
  it had not — it completed (exit 0, 62/62, restored copy green), so it was not re-run.
- 2026-10-20 commit messages are passed with `git commit -m …` (no heredoc); file edits only through Edit / Write. One
  stray `sed -i` replaced line 1 of this file at 15.a (a mechanical STATE-line change, checked in the diff).

## Step 15 — [status: done]
- Executor: executor-opus (dispatched as Framework v2, [model: opus] [effort: high])
- Model: opus (claude-opus-5-5); every commit's trailer `Co-Authored-By: Claude Opus 5.5`
- Mode: single-executor in its own worktree (MediaVault-s15, branch tmp/c25_step15), pathspec WIP commits per the PLAN's
  in-step checkpoint protocol (the dispatch's instruction). The main folder was never touched.
- Files changed: tests/smoke/test_smoke_all_commands.py (Group 5 — IMP-C25, +681 lines, purely additive: 20 smokes in
  4 classes + self-contained helpers + imports; no existing line changed), tests/test_c25_regression_pins.py (new, 39
  tests), .candidates/c25-step15/STATUS.md (this file), .candidates/c25-step15/DISARM.md (new). Nothing else: no
  product code, no conftest, no existing test, no PROGRESS.md.
- Outcome: (1) SMOKE 82 → 102 (12.4 s): prep/push capture (+ never confirmed) · `check_photos pending --list` offline ·
  fetch + the post-fetch merge of a seeded learned sidecar (+ idempotent) · `check_photos --approaches best` in-process
  on the fake Google Photos (download-free, allowlisted rpcids, only its run dir) · `map_photos` dry run on a fresh
  synthetic inventory (mapped + possible-loss, no adb, only its run dir) · an incoming-mode fake fetch (the download
  landed in `restore\.mv_incoming`, the folder is gone, the redirected ~/Downloads byte-identical, journal clean) ·
  `mvmeta_upgrade` dry run over mock_device's tree (v1 → v2 proposed, read verbs only, nothing written anywhere) ·
  `resolve_photos` refused without a terminal (nothing launched, nothing recorded) · `fetch_cleanup` dry run (lists
  the family leftovers + a marked incoming leftover, never an unrelated file; scan scope in tmp; writes nothing) · the
  six push-family verbs through main.py's REAL dispatcher (each reaches its end state and the hook exactly once; no
  child of any kind) · an alias sweep over the new passes (iterators, failed list, pending, fetch_cleanup, check alias
  scope, map alias scope + an engine record naming the alias, mvmeta alias scope) · all four prefixes from push to
  confirmed (each block in its own library file). (2) PINS: a registry of the 24 existing pins (by file + name — a
  deleted / renamed pin fails, nothing re-asserted) + the missing ones: fetch_restore's (= the web action's) spawned
  argv ×4, the P-13 marker literals on both sides (the exact source of every marker-bearing literal in mainfetch, main
  and the other child modules ×6; the web parser's three patterns + signals), calculate_file_hash's signature / lowercase
  64-hex / None-never-raise on a read error, the mvmeta v1 document's key order and exact byte form ×2. (3) DISARM
  62/62 (G1–G15 55/55, S 7/7) on an isolated copy — DISARM.md.
- Key decisions: design decisions 1–6 above. Deviations, each surfaced: (D1) resolve_photos is not in the alias sweep
  (its first act is the TTY refusal; its `--id <alias>` selection is pinned in test_resolve_photos); (D2) the P-13
  producer pin also freezes main.py's two cmd_restore split lines (a web fetch_restore job parses the restore phase
  too) and asserts that gprpc / gpweb / mvcommon / gpcapture carry no marker; (D3) the pins' registry checks
  existence by file text (no import of the focused modules, so it costs nothing and cannot import-break); (D4) the
  probe copy alone got an autouse adb safety net (no repo change) — two probes switch a device check on; (D5) 62
  probes instead of one per guard: 2–6 per guard, plus 7 proving Step 15's own tests bite; (D6) 15.f0 fixed this
  step's own smoke stand-ins (device_inventory swallows a raise → record + assert the list) and 15.f2 strengthened the
  map smoke after the precision pass showed it could not catch G7a; (D7) the guard pair "never-delete-foreign" /
  "deletions only inside .mv_incoming / run-new family" is split by what each protects (design decision 6).
- Contracts for later steps — 16 (docs): docs/testing-strategy.md could list the smoke's Group 5 helpers
  (`_c25_wire` = mainfetch in-process on the fake world; `_c25_record_adb`; `_C25ReadOnlyAdb`; `_c25_dispatcher` = the
  dispatcher compiled from main.py's tail) and the rule "a stand-in inside a swallowing path must record, not only
  raise". 18 (checklist / PR test plan): smoke 102 in 12.4 s; tests/test_c25_regression_pins.py is the one place that
  names every C25 pin; disarm 62/62 (DISARM.md). R2 starts on a green suite: 3278.
- Verification: per sub-task targeted runs (see 15.a–15.f); disarm 62/62 (`<scratchpad>\disarm_15.py`, a `git archive`
  copy of fc4be59; baseline and restored copy 1334 passed; every file restored byte for byte) + the precision pass;
  full suite 3278 passed / 250.6 s and then smoke 102 passed / 12.4 s on 32f6143, sequential; hermeticity listing
  identical; `git diff 068212e..HEAD -- tests/` 0 removed lines; no product file in the diff.
- Self-review (V2 delta 4): (a) PLAN Step 15 — every smoke item (12/12), every named pin (8/8: four by registry +
  additions, four with new pins), every guard of the list (15/15, each ≥ 2 probes, all caught) and the acceptance
  (full + smoke green, runtime recorded); (b) consumers — R2 (green suite), Step 16 (the helpers + the swallowed-raise
  rule worth documenting), Step 18 (the pins file + DISARM.md for the checklist and the PR test plan); (c)
  change-gates — no rollback symbol, ENTRY_TYPE_KEYS untouched (its guard green in the full run), no product code,
  smoke green and under 30 s. Correctly routed as opus/high (no misroute).
