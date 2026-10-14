STATE: IN PROGRESS — next: 14.a (Chrome robustness, follow-up c)

# IMP-C25 Step 14 — `--manual`, `resolve_photos` (manual confirm + match), robust fetch_restore resume, web behavior
# (+ follow-ups a–d) — resumable status

- Plan: docs/feature-fetch-datetime/PLAN.md Step 14 + "Follow-ups carried into Step 14" a–d (+ P-5, P-8, P-10, P-12,
  P-13, P-18, P-20, OD-5, OD-14, D3, Goal 2) · DECISIONS.md §1c–§1f (the user's Chrome rules: close Chrome and its
  tabs properly; never show "Restore pages?") · contracts from Steps 10 / 11 (root STATUS.md of the main folder),
  12 + picks (.candidates/c25-step-12/C/*), 13 / 13b / 13c (.candidates/c25-step13*/STATUS.md).
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-s14` · branch `tmp/c25_step14` · base c6b53c6 (Steps 0–13c done).
  The main folder is never touched (the user archives from it).
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe -m pytest …`, run from the worktree.
  Gates run SEQUENTIALLY. Baseline (orchestrator re-run at c6b53c6): full suite 3062 · smoke 82.

## Resume rule
Read this file, then `git log --oneline -15` (WIP commits: `wip(imp-c25): step 14.<x> — …`) and `git status`. An
uncommitted diff belongs to the first unticked sub-task: keep it when coherent and its targeted tests pass, else
`git checkout -- <its files>` (record that here). Continue at the first unticked sub-task. Commit THIS file
(force-added: `.candidates/` is gitignored) with every WIP commit; tick a sub-task only after its tests pass, with its
SHA. Pathspec commits only; never -A, amend, rebase or stash. Never edit PROGRESS.md (the orchestrator does).

## Sub-tasks (one WIP commit each)
- [ ] 14.0 this status file: the sub-task plan + the design decisions below (no code)
- [ ] 14.a follow-up c — Chrome robustness (mainfetch, tools/warm_profiles.py, tools/gp_inventory.py connect):
      `_ProcessTable.query` with `-ErrorAction Stop` (a WMI failure is "cannot tell", never "gone") · the shared close
      under load: `_terminate_session_chrome` takes the open Popen handle as the identity proof when the process table
      cannot tell (or says gone while the handle says running), taskkill /T /F then the handle's kill() — never a pid
      the table names with another profile or an unreadable command line (Step 11's pins kept) · `init_driver`: a
      failed attach (chromedriver-manager / the attach / the Photos tab, Ctrl+C included) ends the Chrome it just
      launched (`_end_launched_chrome`) · a bounded page-load timeout for every session (`bound_page_loads`,
      FETCH_PAGE_LOAD_S, before the first navigation; the crawler's connect too) · warm_profiles closes the Chrome it
      launched (`_close_session`, last=True: an attached Chrome only loses this run's tabs) and refuses to attach to a
      previous profile's Chrome (port guard only when that Chrome may still run) — tests: tests/test_chrome_robustness.py
- [ ] 14.b follow-up d — the RPC quality flags: ONE read-only live probe (TV profile, one known item, port 9222 free,
      no fetch/crawl running, nothing downloaded, Chrome closed, no URL/cookie/email printed) of the raw item records
      (swbisb / lcxiM / VrseUb / EWgK9e) → fix gpweb's parsers where the probe establishes a position with certainty,
      else keep "unknown" and say so — tests: tests/test_rpc_quality_flags.py (+ the fake updated only where the probe
      proved the real layout)
- [ ] 14.c `--manual` + the identification block (mainfetch): at the end of `_fetch_items`, before `_finish_downloads`,
      every object of the entry part not fetched gets its identification block (account, exact uploaded name, local
      taken time, content key, size, what was tried, its failed-list status, the CLI next steps); with ctx.manual the
      same block + the browser at the best start + a per-object watch of ctx.target["dir"] (MANUAL_WAIT_S = 30 min,
      harvest_downloads(timeout=, stop=)), SHA-256 verification, learned record source `manual` (proof sha256); the
      `failed` ones are taken too (follow-up a) · usage lines name `--manual` — tests: tests/test_fetch_manual.py
- [ ] 14.d robust fetch_restore resume (mainfetch): a re-run goes straight to S0 — the session account's learned
      sidecar (photo_ids proven but not merged yet: an interrupted parent never merged) and the fetch journals (the
      photo_ids an interrupted run located) add S0a candidates (still verified, P-5) · public helpers for resolve_photos:
      `locate_object(..., stages=)`, `verify_item`, `open_best_start`, `object_locators` (extracted from
      `_check_locators`, check unchanged) — tests: tests/test_fetch_manual.py (resume) + tests/test_resolve_photos.py
- [ ] 14.e mvcommon: the resolve answer log `resolved.jsonl` (append_resolved / read_resolved: resume + the IMP-C27
      "not uploaded" list) and `read_failed_csv_rows` (every object row of a failed_objects.csv / resolutions_todo.csv,
      sharing read_failed_csv's Excel-tolerant decoding, read_failed_csv unchanged) — tests: tests/test_resolve_photos.py
- [ ] 14.f main: `resolve_photos [failed|<csv>] [--account a] [--id <manual_id>]` — the TTY refusal, the rows (newest
      failed row per object, confirmed / orphaned / answered ones left out), grouped by account, one Chrome session per
      account under the fetch lock (start-up cleanup + journal + robust close), the identification block, the best
      start, `[p]aste photo URL | [s]earch term | [n]ot uploaded | s[k]ip | [q]uit`, every answer recorded at once, the
      final dry-run summary + `Apply these N changes to the library? [y/N]`, the download-lost re-fetch offer, the C27
      list · check_photos' report points ambiguous objects at resolve_photos (follow-up b) · the CLI branch + usage
      line — tests: tests/test_resolve_photos.py
- [ ] 14.g gates: disarm probes (isolated scratch copy) · full suite · smoke · self-review · STATE: COMPLETE · the
      final feat commit `feat(imp-c25): Step 14 — --manual, resolve_photos, robust fetch_restore resume, Chrome robustness`

## Design decisions (binding for the rest of this step — recorded early so a resumed session stays consistent)
1. Legacy mode keeps today's transcript: the identification block and `--manual` act only when the entry's downloads
   go through a real Chrome (ctx.target mode incoming | fallback). A driver without Chrome's DevTools protocol (None or
   a test stand-in — never a real Chrome) keeps today's output byte for byte (Step 11's rule): every frozen oracle of
   test_fetch_harvester / test_fetch_account_routing stays untouched.
2. `--manual` (CLI only — the web never passes it) runs at the END of `_fetch_items` (after `_record_unfetched` /
   `_record_wrong_items`, before `_finish_downloads` empties the folder): every object of the entry part still not
   fetched — `failed` ones included (follow-up a) — gets, one at a time: its block, the session's Photos tab at its
   best start (its located item's page · the user's manual match · the first candidate the stages saw · the day search
   of its expected local date · the search of its exact uploaded name; nothing is clicked or downloaded), and a watch
   of ctx.target["dir"] (the folder the automation Chrome saves into) for up to MANUAL_WAIT_S = 30 min. The round's
   queue holds EVERY still-unfetched object of the entry as pending (a download of another one is never deleted as a
   wrong item) and ends when the current one is fetched (harvest_downloads(..., timeout=, stop=)). No stdin is read.
   Only a SHA-256 match is kept (the harvester); a wrong item is deleted (incoming mode) and the wait goes on.
3. A `--manual` download is recorded in the learned sidecar with proof `sha256` (its BYTES matched — the strongest
   download proof, so the block learns the content key) and source `manual` (the user's hand: the PLAN's "learned
   manual proof"): `confirmed` is never set by it (P-5: a manual result is confirmed by a later check). Its photo_id is
   recorded only when the item opened for it holds exactly those bytes (Step 10's rule: item['_trigger']['record']).
4. A `manual` match (resolve_photos' accepted mismatch) is NEVER downloaded by the fetch's own stages: D3 ("later
   fetches go direct and still verify before downloading") and Goal 2 ("never downloads an unverified item in the new
   stages") forbid it. S0a still tries it (verified as always); `--manual` opens it first; the identification block
   says so. The resolve prompt keeps the PLAN's words — "a later fetch verifies it by SHA-256" = `fetch <id> --manual`.
5. resolve_photos never offers a manual match for a PROVEN content mismatch (the item's dedupKey differs from the
   object's content key: its bytes are not the object's — a SHA-256 check would refuse them); it says so instead.
6. resolve answers: a paste VERIFIED download-free (dedupkey / filename — the very P-5 check check_photos runs) is a
   learned record with source `check` (so the apply can confirm it and the object leaves the failed list); an accepted
   manual match: proof `manual`, source `manual` (never confirms by itself); a search term: `search_override` (source
   `manual`, P-10) tried at once through S3u alone — a verified find adds a `check` record with approach
   s3u_user_query. "Not uploaded" and every other answer also go to the answer log `resolved.jsonl` (fsynced at once):
   resume ("answered rows are skipped" — an answer at or after the object's newest failed row) and the IMP-C27 list.
   Skip and quit record nothing (a skipped row comes back).
7. resolve's apply = the apply engine over the WHOLE learned sidecar (as check_photos --apply and the post-fetch merge
   do): first its dry-run summary (per changed object; this session's marked), then `Apply these N changes to the
   library? [y/N]` (EOF / anything but y = no). Declined: the library is untouched; the answers stay in the sidecar
   (P-10) — the next fetch's merge, check_photos --apply or resolve_photos folds them (said in the decline line).
8. download-lost rows (Step 10: the item WAS found, its transfer broke twice) are not re-identified: they are listed
   before the walk with their fetch commands, and at the end (Chrome closed, lock released) "Re-fetch them now?
   [y/N]" runs the normal `fetch` (cmd_dispatch_fetch) per id — the question says that a fetch folds the learned
   sidecar when it ends.
9. resolve_photos refuses when stdin is not a TTY (exit 1); it takes the fetch lock WITHOUT waiting; one Chrome per
   account (GP_ACCOUNTS order), the fetch's start-up cleanup first and a fetch journal (crash cleanup), closed through
   the shared close (last=True for the last account) and `_end_run`.
10. Resume (fetch): the learned sidecar and the fetch journals only ADD S0a candidates (after the block's own photo_id
   and alternates; dead ids left out) — S0a verifies each (P-5) before anything is requested; a locator that gains
   nothing is passed unchanged (the same object), so today's runs are byte-identical. check_photos and locate are not
   affected (they call locate_object directly). The parent never merges before the child (Step 6/10 pins: no library
   write after a Ctrl+C; the merge line after the child's output).
11. The shared close (follow-up c): the Popen handle the run holds is the identity proof when the process table cannot
   answer — while it is open the pid cannot have been reused, and the run launched that chrome.exe with that profile.
   A positive answer naming another profile, or an unreadable command line, still means "never" (Step 11's pins).
   The start-up cleanup (journaled pids, no handle) keeps the command-line rule unchanged.

## Assumptions (also listed in the final report)
- (A1) The harness's commit-attribution notice names the model for each commit's trailer (it said Opus 5.5 at dispatch;
  the session environment reports claude-fable-5-1; no MODEL-FALLBACK banner) — the quirk Steps 8–13c recorded.

## Log
- 2026-10-14 dispatched; read-in: base contract (executor-opus.md); PLAN (journal, P-1…P-20, OD table, protocol, Steps
  0–18 incl. 14 + follow-ups a–d, consumer rows, risks, §M4–M18); DECISIONS (§1–§1f); PROGRESS; root STATUS.md Steps
  10 / 11 / live wrong-item pick (main folder, read only); .candidates/c25-step-12/C/{CANDIDATE,PICKS}_STATUS.md;
  .candidates/c25-step13{,b,c}/STATUS.md; mainfetch.py (whole file); gprpc.py; gpweb.py; mvcommon.py identity section;
  main.py (apply engine, merge, dispatch/fetch_restore, map_photos resolutions + appends, check_photos report/apply/
  scopes/spawn, the dispatcher's fetch branches + usage); webui/server.py actions + progress parser;
  tools/warm_profiles.py; tools/gp_inventory.py session/connect; tests/conftest.py; tests/gphotos_fake.py;
  docs/testing-strategy.md; the pins in test_fetch_harvester / test_fetch_hygiene / test_fetch_photos_tab /
  test_chrome_restore_bubble / test_warm_profiles / test_gphotos_apply / test_check_photos / test_locator /
  test_fetch_search_wait / test_cli_parsers.
