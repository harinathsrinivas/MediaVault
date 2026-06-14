# IMP-C15 — Decisions (micro-robustness batch)

Load-bearing choices for `fix/micro_robustness_c15`. Companion to `PLAN.md` in this folder.

## D-1 — Both bugs ship in ONE PR
The user confirmed Bug 1 (repair_dummies atomic swap) and Bug 2 (`_verify_chunk_hash` garbled-stdout guard) are fixed together in a single PR (IMP-C15 covers both). They are independent, surgical, and low-risk; no reason to split.

## D-2 — Bug 2 garbled/empty-stdout behavior = Lenient / hex-validate (RESOLVED 2026-06-14 by user)
On empty OR garbled device stdout, `_verify_chunk_hash` validates the first whitespace-split token against `re.fullmatch(r"[0-9a-fA-F]{64}", first)`. If it does not match (empty or non-hex) → print a warn-and-skip line and `return` (keep the push alive). ONLY a well-formed 64-hex hash that DIFFERS from `expected_sha256` raises `subprocess.CalledProcessError` (→ unchanged IMP-C2 retry → eventual fail). Rejected alternative: a "strict" mode that treated empty/garbled as a mismatch-and-raise — rejected because it would abort a push on a benign device quirk, the exact behavior the function's docstring promises NOT to do.
- `re` is already imported at `main.py:6` — no new import.
- Case-sensitive equality (`first != expected_sha256`) preserved on the well-formed path — surgical, matches today's semantics (sha256sum + hashlib both lowercase).

## D-3 — Bug 1 swap idiom = single `os.replace(tmp_path, current_path)`
Replaces the non-atomic `os.remove(current_path)` + `os.rename(tmp_path, current_path)` at `main.py:2060-2061`. This is the EXACT idiom already used at `main.py:469` (`make_video_dummy`) and the IMP-C9 atomic-swap lesson. `tmp_path` is same-directory/same-filesystem so `os.replace` is atomic on NTFS. No new lock-retry logic added (matches the `make_video_dummy` precedent, which also does not retry the atomic write).

## D-4 — NOT change-gated (rollback)
Ran the auto-rollback change-gate. Neither fix alters journal format/durability, PONR locations / `mark_point_of_no_return()`, what gets recorded, `recover_journal()` semantics, season resume-range messaging, or the `RollbackHardFail` contract. `_verify_chunk_hash` runs inside cmd_push's push→mv→verify closure but only NARROWS a failure path (IndexError → warn-and-skip); the well-formed-mismatch raise the closure depends on is unchanged. `cmd_repair_dummies` is not journaled. Conclusion: no rollback sign-off required.

## D-5 — explicit `multi_ep_alias` skip in `cmd_repair_dummies` = INCLUDE (RESOLVED 2026-06-14 by user)
`cmd_repair_dummies` is a whole-library iterator that today skips only `season_map` and is alias-safe only BY ACCIDENT (aliases lack a `status` key, so the `status != "archived"` guard filters them before `entry['folder_path']` is read). The CLAUDE.md ENTRY_TYPE_KEYS guardrail wants every whole-library iterator EXPLICITLY alias-safe. Since Step 1 already edits this function, adding `if entry.get("type") == "multi_ep_alias": continue` is a ~1-line in-scope hardening. DECISION: INCLUDE — now baked into Step 1 (the skip after the season_map skip), Step 3 (a `sandbox_alias` test case asserting the explicit skip), and Step 5 (the status-line clause). The existing smoke `test_repair_dummies_alias` continues to cover the path as well.

## D-6 — Test placement
- Bug 2 cases extend `tests/test_cmd_push_verify.py` (reuses `GOOD_HASH`/`BAD_HASH`); tested via a DIRECT unit call of `main._verify_chunk_hash` with a monkeypatched `subprocess.run` (cleaner than threading odd stdout through the whole cmd_push closure).
- Bug 1 in NEW `tests/test_repair_dummies.py` using the existing `sandbox` + `fake_dummy` fixtures; includes a regression guard that `os.remove` is NOT called on `current_path` (catches a future revert to remove+rename).
- No `conftest.py` change needed (no binding-hazard patch) → no opus step; test writes are sonnet.
