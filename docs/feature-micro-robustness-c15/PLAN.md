# Task: IMP-C15 — Micro-robustness batch (repair_dummies atomic swap + _verify_chunk_hash garbled-stdout guard)

Suggested branch: fix/micro_robustness_c15

## Summary

IMP-C15 ships two independent, surgical, low-risk hardening fixes in `main.py`, in ONE PR. (1) `cmd_repair_dummies` currently swaps a regenerated dummy into place with a non-atomic `os.remove(current)` + `os.rename(tmp, current)` (`main.py:2060-2061`); a crash/Ctrl-C/Windows lock between the two lines leaves NO file at the path and a stranded `.repair_tmp` orphan — the next repair run then sees "Missing" and skips it. Replace both lines with a single atomic `os.replace(tmp_path, current_path)` (the exact idiom already used at `main.py:469` in `make_video_dummy`, the IMP-C9 lesson). (2) `_verify_chunk_hash` (`main.py:1254`, DORMANT — only runs under `PUSH_VERIFY_REMOTE=True`, gated off today) parses `result.stdout.strip().split()[0]`, which `IndexError`s on empty device stdout; that error is NOT in the IMP-C2 `retry_on=(CalledProcessError,)` set, so it escapes the retry wrapper as a raw traceback and aborts the push mid-season — violating the function's warn-and-skip docstring promise. Per the user's RESOLVED 2026-06-14 decision (Lenient / hex-validate), validate the first token is a clean 64-hex sha256: empty OR garbled → warn-and-skip `return`; only a well-formed hash that DIFFERS from `expected_sha256` raises `CalledProcessError` (→ unchanged C2 retry path). Neither fix is change-gated (see Change-gate check below).

## Change-gate check (CONCLUSION: NOT change-gated — no rollback sign-off required)

Ran the auto-rollback change-gate (CLAUDE.md / `docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §10) mentally against both fixes. Neither alters: the journal format/durability (`fsync` + `os.replace` of `.mediavault_txn.json`), the PONR locations or `mark_point_of_no_return()` placement, what gets recorded (created-this-run / D-6 / D-7 scoping), `recover_journal()` semantics, the season resume-range messaging, or the `RollbackHardFail` contract.
- `_verify_chunk_hash` runs INSIDE cmd_push's push→mv→verify closure, but this change only NARROWS a failure path (IndexError → warn-and-skip on empty/garbled stdout); the well-formed-mismatch path still raises `CalledProcessError` exactly as before, so the C2 retry behavior the closure depends on is byte-for-byte unchanged. No rollback machinery is touched.
- `cmd_repair_dummies` is not journaled at all (no `RollbackJournal`, no PONR). The swap goes from non-atomic to atomic, which strictly reduces failure windows.

Conclusion: this task does NOT trigger the rollback change-gate; no user sign-off on rollback grounds is required.

## Context

`cmd_repair_dummies` regenerates undersized archived dummies in bulk (a 2026-05-27 sweep regenerated 423 in one run — i.e. 423 non-atomic swap windows). `_verify_chunk_hash` is the IMP-C8 post-push device-side sha256 check, gated off behind `PUSH_VERIFY_REMOTE` until IMP-A5 lands a config file. Both fixes apply safety idioms the codebase already adopted elsewhere (C9 atomic `os.replace`; OD-2a warn-and-skip) to the two spots that missed them. `re` is already imported (`main.py:6`); no new import is needed.

## Goal

- `cmd_repair_dummies` never has a window in which `current_path` has no file: the swap is a single atomic `os.replace`, and no `.repair_tmp` orphan can survive a successful regenerate.
- `_verify_chunk_hash` never raises on empty/garbled device stdout: it warns and returns (push stays alive). A well-formed-but-different hash still raises `CalledProcessError`; a well-formed-and-equal hash still returns cleanly. Existing case-sensitive comparison semantics preserved on the non-garbled path.
- Both behaviors are covered by deterministic unit tests using existing fixtures/stubs; the smoke gate and full suite stay green.
- IMP-C15 is marked done in `improvements_tierC.md`, `PRIORITY.md`, and the priority graph (all three agreeing).

## Files affected

- `main.py` — `cmd_repair_dummies` (Bug 1 atomic swap + explicit `multi_ep_alias` skip), `_verify_chunk_hash` + its docstring (Bug 2 hex-validate guard).
- `tests/test_cmd_push_verify.py` — add Bug 2 regression cases (empty/garbled stdout).
- `tests/test_repair_dummies.py` (NEW) — Bug 1 atomic-swap regression test (+ optional alias case).
- `ARCHITECTURE.md` — §7.5 push-verify wording (~826-834), §7.6 dummy-system wording (~853, ~936-952). (architect agent)
- `README.md` — only if the architect judges the `repair_dummies` row (line 138) or test-coverage line (line 294) needs a wording touch; substantive doc work is in ARCHITECTURE.md. (architect agent)
- `improvements/improvements_tierC.md` — mark IMP-C15 status `done`.
- `improvements/PRIORITY.md` — move C15 to DONE, bump count, update Last-updated + 👉 NEXT pointer.
- `docs/priority-graph/priority-graph.html` — set C15 node status to done; update the ⚡ Next banner.

## Approach

Two tiny, mechanical-to-standard edits to two helpers in `main.py`, each backed by a focused regression test that pins the new behavior AND guards against reverting to the old one (a spy that `os.remove` is NOT called on `current_path` for Bug 1; an explicit empty-stdout case asserting no raise for Bug 2). Then the architect updates the two ARCHITECTURE.md anchors to describe the documented behavior change, and the tracking trio (tierC / PRIORITY / graph) is flipped to done in lockstep. No new data contracts, no shared-shape changes (see Consumer Impact note below — none required).

## Steps

- [ ] 1. [model: sonnet] [effort: medium] Bug 1 — atomic dummy swap in `cmd_repair_dummies` + explicit `multi_ep_alias` skip (Open Decision (b) RESOLVED 2026-06-14: INCLUDE).
  - Files: `main.py`
  - Details: At `main.py:2060-2061`, replace the two lines
    ```python
    os.remove(current_path)
    os.rename(tmp_path, current_path)
    ```
    with the single atomic call:
    ```python
    os.replace(tmp_path, current_path)
    ```
    `tmp_path` (`current_path + ".repair_tmp" + ext`, line 2052) is in the SAME directory as `current_path` → same filesystem → `os.replace` is safe on Windows NTFS. This mirrors the existing idiom at `main.py:469` (`make_video_dummy`) and the IMP-C9 atomic-swap lesson. Do NOT change anything else in the loop (the counters, the missing/skip guards, the `make_video_dummy` call). Surgical: only these two lines collapse to one.
    REQUIRED (Open Decision (b) RESOLVED 2026-06-14: INCLUDE): also add an explicit alias skip near the top of the per-entry loop, immediately after the existing `season_map` skip at `main.py:2028-2029`, mirroring its style:
    ```python
    if entry.get("type") == "multi_ep_alias":
        continue
    ```
    This makes the whole-library iterator explicitly alias-safe per the CLAUDE.md ENTRY_TYPE_KEYS guardrail (today it is alias-safe only by accident — `multi_ep_alias` entries lack a `status` key, so the `status != "archived"` guard at line 2032 filters them before `entry['folder_path']` is dereferenced at line 2036). One line, no behavior change for non-alias entries.
  - Acceptance: The function does a single `os.replace(tmp_path, current_path)` and no longer calls `os.remove`/`os.rename` for the swap; `python -m pytest tests/smoke/test_smoke_all_commands.py -q -k repair_dummies` stays green; `python -m pytest tests/test_repair_dummies.py -q` (Step 3) passes including the os.remove-not-called guard. verify: `python -m pytest tests/test_repair_dummies.py tests/smoke -q`

- [ ] 2. [model: sonnet] [effort: medium] Bug 2 — hex-validate guard + docstring in `_verify_chunk_hash`.
  - Files: `main.py`
  - Details: At `main.py:1254`, replace the single line
    ```python
    remote_hash = result.stdout.strip().split()[0]
    if remote_hash != expected_sha256:
        raise subprocess.CalledProcessError(
            1, f"hash mismatch for {os.path.basename(remote_path)}"
        )
    ```
    with the lenient hex-validating shape (RESOLVED user decision 2026-06-14):
    ```python
    parts = result.stdout.split()
    first = parts[0] if parts else ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", first):
        print(f"  ⚠️  sha256sum produced no usable output on device — remote verification skipped for {os.path.basename(remote_path)}")
        return
    if first != expected_sha256:
        raise subprocess.CalledProcessError(
            1, f"hash mismatch for {os.path.basename(remote_path)}"
        )
    ```
    Rules (all confirmed against the live file):
    - `re` is ALREADY imported (`main.py:6`) — do NOT add an import.
    - Leave the existing `except subprocess.CalledProcessError:` arm (command-not-found warn-and-skip, lines 1250-1252) UNTOUCHED.
    - Match the warning STYLE of the existing message at `main.py:1251` (`  ⚠️  sha256sum unavailable on device — remote verification skipped for {basename}`) — same two-space indent, same emoji, same "remote verification skipped for {basename}" tail; only the cause clause differs ("produced no usable output" vs "unavailable").
    - Preserve case-sensitive comparison on the well-formed path: keep `first != expected_sha256` (do NOT switch to a case-insensitive compare). Surgical.
    - Update the function docstring (lines 1236-1244) to note that empty/garbled stdout (a well-formed 64-hex first token is required) is ALSO a warn-and-skip path, alongside the existing command-not-found note — so the docstring matches the new behavior.
  - Acceptance: empty stdout (exit 0) no longer `IndexError`s — it warns and returns; a garbled token warns and returns; a valid hash == expected returns None; a valid hash != expected raises `CalledProcessError`; the command-not-found arm still warns and returns. verify: `python -m pytest tests/test_cmd_push_verify.py -q`

- [ ] 3. [model: sonnet] [effort: medium] Tests — Bug 2 cases (extend existing file) + Bug 1 atomic-swap regression (new file).
  - Files: `tests/test_cmd_push_verify.py`, `tests/test_repair_dummies.py` (NEW)
  - Details:
    Constraints (MUST hold in this step): "Never touch real C:\\Media files or real library_*.json." and "Run `python -m pytest -q` and fix failures before marking the step done." Use existing fixtures/stubs only — do NOT add conftest fixtures (no binding-hazard patches needed here, so no `conftest.py` change → no opus needed).

    BUG 2 — add to `tests/test_cmd_push_verify.py`. The cleanest level is a DIRECT unit test of `main._verify_chunk_hash` (it takes `(adb_base, remote_path, safe_path, expected_sha256)` and only calls `subprocess.run`), monkeypatching `main.subprocess.run` to return a stub object with a `.stdout` string. This avoids threading odd stdout through the whole `cmd_push` closure and pins the helper's contract exactly. Add a small local helper that builds a fake `subprocess.run` returning `type("_R",(),{"returncode":0,"stdout":<S>})()`. Cases (use `capsys` to assert the warn line; `pytest.raises` for the raise):
      - empty stdout, exit 0 (`stdout=""`) → `_verify_chunk_hash(...)` returns None, NO raise, and prints "remote verification skipped" (this is the IndexError regression — assert no exception is raised).
      - garbled stdout, exit 0 (`stdout="sha256sum: applet not found\n"`) → returns None, NO raise, prints the skip warning (Lenient/Option-A behavior).
      - valid hash == expected (`stdout=f"{GOOD_HASH}  /path\n"`, `expected_sha256=GOOD_HASH`) → returns None, NO warning, NO raise.
      - valid hash != expected (`stdout=f"{BAD_HASH}  /path\n"`, `expected_sha256=GOOD_HASH`) → raises `subprocess.CalledProcessError`.
      - subprocess raises `CalledProcessError` (command-not-found): monkeypatch `subprocess.run` to raise `CalledProcessError(127, argv)` → returns None, NO raise, prints "sha256sum unavailable" (the existing arm — guards it stays intact). `GOOD_HASH`/`BAD_HASH` already exist in this file (`"a"*64` / `"b"*64`). The existing through-cmd_push FakeAdbVerify tests (a–e) stay as-is.

    BUG 1 — NEW file `tests/test_repair_dummies.py`. Seed a sandbox library via the existing `sandbox` fixture (inspect how `tests/conftest.py`'s `sandbox`/`sandbox_entry` and the smoke `_seed_single` build entries). Build an `archived` leaf entry whose `folder_path`/`filename` point at a real on-disk file under `sandbox["media_dir"]` that is SMALLER than `main.DUMMY_MAX_BYTES` and has a `VIDEO_EXTENSIONS` extension (e.g. write `b"tiny"` to `<media_dir>/test_movie.mkv`); write the entry into `sandbox["lib_movies"]` (id prefix `mov` so it routes there) and `"{}"` into `lib_series`/`lib_anime`. Use the `fake_dummy` fixture (already in conftest — replaces `main.make_video_dummy` with a stub that writes `FAKE_DUMMY_BYTES` to the tmp path and returns True; this is exactly what makes the regenerate path deterministic without ffmpeg). Test body:
      - Run `main.cmd_repair_dummies()`.
      - Assert the file at `current_path` exists and its bytes == `FAKE_DUMMY_BYTES` (import `FAKE_DUMMY_BYTES` from `conftest`, as the smoke test does at `tests/smoke/test_smoke_all_commands.py:353`) — proves the swap landed the regenerated dummy.
      - Assert NO `.repair_tmp` orphan remains in `media_dir` (e.g. `list(media_dir.glob("*.repair_tmp*")) == []`).
      - REGRESSION GUARD that the swap uses `os.replace` not remove+rename: monkeypatch/spy `main.os.remove` to record calls, run, and assert it was NOT called with `current_path` (a future revert to `os.remove(current)` + `os.rename(...)` re-introduces the window and this test goes red). Keep the spy a thin wrapper that still delegates to the real `os.remove` so unrelated cleanup is unaffected, or simply assert `current_path` is not in the recorded-removed paths.
      - REQUIRED (Open Decision (b) RESOLVED 2026-06-14: INCLUDE): add a case using the existing `sandbox_alias` fixture (seeds a `multi_ep_alias` chain) and assert `main.cmd_repair_dummies()` runs clean (no KeyError) and does not touch the alias entry. NOTE: the smoke suite already has `test_repair_dummies_alias` (`tests/smoke/test_smoke_all_commands.py:566`) asserting the alias is skipped via the no-`status` path — this added case asserts the EXPLICIT skip is in place.
  - Acceptance: all new cases pass; `python -m pytest tests/test_cmd_push_verify.py tests/test_repair_dummies.py -q` green; no real `C:\Media` / real `library_*.json` touched. verify: `python -m pytest tests/test_cmd_push_verify.py tests/test_repair_dummies.py -q`

- [ ] 4. [model: sonnet] [effort: medium] Docs — the architect agent updates `ARCHITECTURE.md` (+ `README.md` if warranted) for the documented behavior change.
  - Files: `ARCHITECTURE.md`, `README.md`
  - Details: The architect agent updates the two ARCHITECTURE.md anchors to reflect the shipped behavior:
    - §7.5 push-verify (`ARCHITECTURE.md` ~826-834): the warn-and-skip clause currently says only "If `sha256sum` itself is unavailable (non-zero exit), the verifier prints one warning and skips." Extend it to note that warn-and-skip ALSO covers empty/garbled device stdout (the verifier now requires a well-formed 64-hex first token; empty or non-hex output → warn-and-skip, push stays alive), so the documented promise matches `_verify_chunk_hash` after this change.
    - §7.6 dummy system (`ARCHITECTURE.md` ~853 header line and the `cmd_repair_dummies` per-candidate step list ~936-952): in step 5 ("Replace the existing dummy with the regenerated video dummy.", ~952), state that the replace is a single ATOMIC `os.replace(tmp, current)` (no window without a file at the path), matching `make_video_dummy`'s own atomic write described at ~908-912. Optionally cross-reference the IMP-C9 atomic-swap lesson.
    - `README.md`: substantive doc work is in ARCHITECTURE.md. Touch README ONLY if the architect judges the `repair_dummies` row (line 138) benefits from an "(atomic)" note, or the remote-verify test-coverage line (line 294) needs adjustment. Do not over-edit; README has no behavioral push-verify section.
    Keep edits surgical — describe the two behaviors, do not restructure the sections.
  - Acceptance: ARCHITECTURE.md §7.5 and §7.6 describe the empty/garbled warn-and-skip and the atomic dummy swap respectively; wording is consistent with the code shipped in Steps 1-2; no unrelated sections changed. verify: re-read the two ARCHITECTURE.md anchors and confirm they match Steps 1-2; `git diff --stat` shows only ARCHITECTURE.md (and optionally README.md).

- [ ] 5. [model: haiku] [effort: low] Mark IMP-C15 done in the tracking trio (tierC + PRIORITY.md + priority graph) — all three must agree.
  - Files: `improvements/improvements_tierC.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`
  - Details: Per the PRIORITY.md maintenance protocol (bottom of that file). Mirror the style of C12/C13/C14's done lines (name the branch + what shipped):
    - `improvements/improvements_tierC.md`: change the IMP-C15 `- Status: pending` line (line ~286) to:
      `- Status: done (fix/micro_robustness_c15 — cmd_repair_dummies non-atomic remove+rename replaced with single atomic os.replace; _verify_chunk_hash hex-validates the device sha256 first token (empty/garbled → warn-and-skip, only a well-formed differing hash raises CalledProcessError); unit tests in tests/test_repair_dummies.py + new cases in tests/test_cmd_push_verify.py; explicit multi_ep_alias skip added to cmd_repair_dummies)`.
    - `improvements/PRIORITY.md`: (a) bump `**Last updated:**` (line 12) to `2026-06-14 (IMP-C15 done — fix/micro_robustness_c15).`; (b) rewrite the `## 👉 SUGGESTED NEXT TASK` block (lines 16-26) to point at the new next task — recommend **IMP-C16** (Band-1 high; see Suggested next tasks below) with a one-line why; keep the R6/R7 decision-awaiting note; (c) remove the IMP-C15 row from Band 0 (line 35) so Band 0 holds only the two 🚦 decision-gated items R6/R7; (d) DONE list (lines 82-88): add `C15` (micro-robustness) to the list and bump the count `## ✅ DONE (15)` → `## ✅ DONE (16)`.
    - `docs/priority-graph/priority-graph.html`: (a) in the `TASKS` array, change the C15 entry (line 159) from `["C15","micro-robustness batch","C","high","todo","repair_dummies atomic swap; verify_chunk IndexError guard"]` to `["C15","micro-robustness batch","C","done","done","Fixed fix/micro_robustness_c15: repair_dummies single atomic os.replace; _verify_chunk_hash hex-validates first token (empty/garbled → warn-and-skip)"]` (set field index 3 priority→`"done"` AND index 4 status→`"done"`, matching the C12/C13/C14 done rows); (b) update the ⚡ Next banner (line 84) to point at the new next task (IMP-C16) instead of IMP-C15, mirroring the existing banner wording. PRIORITY.md and the graph MUST agree on the next task and on C15 being done.
  - Acceptance: IMP-C15 shows done in all three files with a consistent next-task pointer (IMP-C16); DONE count is 16; graph C15 node renders done. verify: `git diff` shows only those three files for this step; re-read each changed region for consistency.

## Risks and edge cases

- `os.replace` cross-volume: not a risk here — `tmp_path` is built as `current_path + ".repair_tmp" + ext` (same directory as `current_path`), so source and dest are always on the same filesystem. `os.replace` is atomic on NTFS for same-volume moves.
- Windows file lock (Plex/Search/AV) on `current_path` during `os.replace`: `os.replace` can still raise `PermissionError` if the destination is locked — but that is STRICTLY BETTER than the old remove+rename, which could leave no file at all. This fix does not add new lock-retry logic (out of scope; the existing `make_video_dummy` atomic write at line 469 also does not, so we match precedent).
- Bug 2 regex: `re.fullmatch(r"[0-9a-fA-F]{64}", first)` rejects a leading/trailing-whitespace token only if whitespace survives `split()` — it won't, since `str.split()` with no args strips and tokenizes on any whitespace, so `first` is always whitespace-free. A 64-hex token with a trailing path (the real `"<hash>  <path>"` format) splits correctly to `parts[0]`.
- Case sensitivity: real `sha256sum` emits lowercase; `expected_sha256` is stored lowercase (Python `hashlib.hexdigest()`). Keeping `first != expected_sha256` (case-sensitive) preserves today's semantics. The regex accepts `A-F` too, so an uppercase-emitting device would pass the hex gate then fail the equality compare → `CalledProcessError` → C2 retry, same as a genuine mismatch. This is the documented pre-existing behavior; not changed by this task.
- Test isolation: the Bug 1 test must use the `sandbox` fixture (dual-patches `mvcommon.LIBRARY_*` AND `main.LIBRARY_*` + redirects `LOCAL_ROOT`) so `load_library`/`save_library` and the C:\Media hard-guard apply. Do NOT DIY the redirect.
- `os.remove` spy: wrap, don't replace — the real `cmd_repair_dummies` no longer calls `os.remove` on the swap path, but other code under test should not be perturbed; assert `current_path not in removed_paths` rather than `os.remove` never called at all, to stay robust.
- Open Decision (b) RESOLVED 2026-06-14 (INCLUDE): Steps 1, 3, and 5 include the explicit `multi_ep_alias` skip + its test case. No longer conditional.

## Consumer Impact Analysis

Not required. Neither fix adds, changes, or removes a shared data contract: no new/renamed/removed library entry type, no new/renamed/removed shared field or key, no ID-shape change, no `status`-value change. `ENTRY_TYPE_KEYS` is unchanged. Bug 1 is an internal filesystem-swap mechanism inside one function; Bug 2 narrows an internal failure path inside one helper. The alias-skip (Open Decision (b), RESOLVED: INCLUDE) only ADDS a defensive `continue` for an already-existing entry type (`multi_ep_alias`) in one iterator — it reads the existing `type` key via `.get()` and consumes no new shape. Therefore no consumer audit table is warranted (the CLAUDE.md ENTRY_TYPE_KEYS guardrail is satisfied by the existing registry plus, by making this iterator's alias-safety explicit rather than accidental).

## Verification

Run from the repo root. The user's environment uses `python -m pytest` (NOT bare `pytest`).

1. `python -m pytest tests/test_cmd_push_verify.py -q` — Bug 2 cases (empty/garbled/valid-eq/valid-neq/cmd-not-found) plus the existing through-cmd_push verify tests.
2. `python -m pytest tests/test_repair_dummies.py -q` — Bug 1 atomic-swap regression (file landed, no orphan, os.remove-not-called guard; + optional alias case if (b) approved).
3. `python -m pytest tests/smoke -q` — smoke gate (every command vs the tiny fixtures incl. a library carrying every entry type). MUST be green and finish < 30s before committing any code-touching step and before the PR. (Steps 1-2 touch `main.py`, so this gate is mandatory per the SMOKE-GATE rule.)
4. `python -m pytest -q` — full suite green.

## Open Decisions

(a) RESOLVED 2026-06-14 (user): Bug 2 garbled/empty-stdout behavior = **Lenient / hex-validate**. Validate the first stdout token against `[0-9a-fA-F]{64}`; empty OR garbled → print warn-and-skip and `return` (keep the push alive); only a well-formed hash that DIFFERS from `expected_sha256` raises `CalledProcessError` (→ unchanged C2 retry → eventual fail). Baked into Step 2.

(b) RESOLVED 2026-06-14 (user chose INCLUDE) — `cmd_repair_dummies` explicit `multi_ep_alias` skip. `cmd_repair_dummies` is a whole-library iterator that today skips ONLY `season_map` (`main.py:2028`). It is alias-safe only BY ACCIDENT: `multi_ep_alias` entries carry no `status` key, so the `if entry.get("status") != "archived": continue` guard (line 2032) filters them out before `entry['folder_path']` is read (line 2036). The CLAUDE.md ENTRY_TYPE_KEYS guardrail says every whole-library iterator must be EXPLICITLY alias/season_map-safe (skip `type == "multi_ep_alias"` or call `_resolve_alias`). Since Step 1 already edits this function, adding `if entry.get("type") == "multi_ep_alias": continue` after the season_map skip is a ~1-line in-scope hardening that satisfies the guardrail and removes the fragility (if a future change ever moves the `status` check or gives aliases a status, the accidental safety would silently break). DECISION: INCLUDE — baked into Step 1 (the skip), Step 3 (a `sandbox_alias` test case asserting the explicit skip), and Step 5 (the status-line clause).

---

# End of plan — branch, PR, manual tests, and next tasks

## Branch name

`fix/micro_robustness_c15`  (repo convention `fix/<slug>`, lowercase, underscores, under 50 chars).

## PR to main

- **Title (MUST include the IMP code):** `fix: repair_dummies atomic swap + verify_chunk_hash garbled-stdout guard — IMP-C15`
- **Body order (per CLAUDE.md `docs/git-pr-conventions.md`):**
  1. The auto-generated Claude Code summary FIRST.
  2. Then a `## Original task prompt` section containing the COMPLETE verbatim initial task prompt (the full IMP-C15 planning request, unedited).
  3. Then the `🤖 Generated with Claude Code` trailer.
- **Checkpoint 1 (human-gated):** merging into `main` is human-gated. Create the PR, then STOP and ask the user for explicit confirmation before `gh pr merge` / merge / push to `main`. Do not merge unprompted.
- Run the full Verification list (esp. `python -m pytest tests/smoke -q` and `python -m pytest -q` both green) BEFORE opening the PR.

## Manual test commands (what the user can run by hand)

Be honest about reproducibility: Bug 1 is hand-runnable; Bug 2 is effectively unit-test-only (the device quirk is hard to reproduce on demand and the feature is gated off).

Bug 1 — `cmd_repair_dummies` atomic swap (HAND-RUNNABLE against a throwaway prefix):
- The fix is observable as "no `.repair_tmp` orphan and a file always present at the path." To feel it safely WITHOUT touching real media, run the unit test and watch it pass: `python -m pytest tests/test_repair_dummies.py -q`.
- If you want a real-binary smoke against an isolated prefix on the live library (ffmpeg required, real `repair_dummies`): pick an `id_prefix` that matches a FEW archived dummies you don't mind regenerating and run `python main.py repair_dummies <id_prefix>`. It regenerates in place atomically; afterwards confirm `dir /s *.repair_tmp*` (PowerShell: `Get-ChildItem -Recurse -Filter *.repair_tmp*`) finds nothing. (Note: regeneration is idempotent in spec, so re-running is safe; this only rewrites dummy placeholders, never real media — real files exceed `DUMMY_MAX_BYTES` and are skipped.)

Bug 2 — `_verify_chunk_hash` empty/garbled guard (PRIMARILY UNIT-TEST-ONLY):
- The verifier only runs when `PUSH_VERIFY_REMOTE=True` (gated off today, flipped per-config in IMP-A5). The empty-stdout device quirk is not reliably reproducible on demand. Rely on the unit tests: `python -m pytest tests/test_cmd_push_verify.py -q` (the empty/garbled cases are the exact regression).
- If you want to exercise it live: temporarily set `PUSH_VERIFY_REMOTE = True` at the top of `main.py`, connect a device, and `python main.py push <small_split_id>` — a normal device emits a valid hash (verify passes); to see the warn-and-skip you would need a device whose `sha256sum` returns empty/garbled output, which is the hard-to-force quirk. Revert the flag afterward. The honest answer: the unit tests are the real coverage here; the live path is the happy path.

## Suggested next tasks (after IMP-C15)

Per `improvements/PRIORITY.md`: with IMP-C15 done, Band 0 holds only the two 🚦 decision-gated rollback findings (R6, R7) — those need a USER DECISION, not code-first work — so the next CODE task is Band 1. Recommended order with why:

1. **IMP-C16 — anime fetch profile routing (high).** Why next: the user has confirmed the real topology is THREE Google accounts (movies/series/anime), but `cmd_fetch_route` still sends `ani-*` to the *series* Chrome profile, so the first real archived-anime restore silently fails (0 thumbnails, looks like a session expiry) — a confusing dead-end for a whole third of the library and a blocker for the couch-vault flow for anime. Small, low-risk (additive profile + one routing branch; movies/series routing unchanged), and it lays the data-driven id-prefix→profile seam that X1/X4 multi-account fetch builds on.
2. **IMP-S1 — stand up Jellyfin (high, ZERO code).** Why next: it is zero-code and delivers immediate couch value (run the existing `JELLYFIN_SETUP_GUIDE.md`), and it can run in PARALLEL with any code task — so it is "free" throughput while C16/A10 land.
3. **IMP-A10 — truth-up `requirements.txt` (high).** Why next: a clean install is half-broken today (missing `requests` / `webdriver-manager`); cheap to fix and unblocks anyone bootstrapping the repo (and CI in IMP-A12).

(Also worth surfacing to the user when convenient: the two 🚦 change-gated rollback decisions R6 — failed restore-merge leaves no file → title vanishes from Jellyfin/Plex — and R7 — re-running after a crash destroys the recovery journal. They sit in Band 0 but need a user-picked option from `improvements_tierR.md`, not code first.)
