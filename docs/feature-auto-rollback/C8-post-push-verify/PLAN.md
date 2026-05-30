# Task: IMP-C8 — Post-push remote verification

Suggested branch: `feature/post_push_verify`

## Context

`cmd_push` (in `main.py`) splits a file into chunks, pushes each chunk to the device
via `adb push`, and (since IMP-G1) atomically renames from `<final>.partial` to the
final name. The local chunk is deleted only AFTER the push succeeds. The local chunk
hash is already computed and stored in `split_info.chunks[i].hash` before the push
loop starts. What is NOT verified: whether the bytes that landed on the device match
the local hash. ADB push has its own integrity checks but is not bulletproof against
certain USB cable / driver issues. Silent corruption can land a corrupt chunk on the
phone and only surface during `cmd_restore` weeks later.

**Confirmed state (do not re-derive):**
- IMP-G1 is merged (`8c12680` in `origin/main`). The remote final path is the
  post-`adb shell mv` name. No pre-G1 code path exists.
- IMP-C2 is merged (`cf79684` in `origin/main`). `retry()` lives in `mvcommon.py`;
  the push+mv pair is already wrapped in `retry(retry_on=(CalledProcessError,))`.
  On hash mismatch, C8 MUST raise `CalledProcessError` so C2's wrapper handles the
  re-push automatically. This is a hard requirement, not conditional.
- IMP-A1 is merged (`1aac738`). `retry()` and all library helpers live in
  `mvcommon.py` which is stdlib-only (no subprocess). Any adb-specific helper for
  C8 belongs in `main.py`, not `mvcommon.py`.

---

## Resolved Decisions (confirmed 2026-05-30)

| # | Question | Decision |
|---|----------|----------|
| OD-1 | Hash algorithm on the device | **`sha256sum`** — direct comparison to stored SHA-256; no extra local re-hash; no schema change |
| OD-2 | Hash command unavailable | **Warn and skip** — print one warning line, continue as if `PUSH_VERIFY_REMOTE=False`; do not abort the push |
| OD-3 | Verification helper location | **Inline in `main.py`** as a module-level private `_verify_chunk_hash()` — keeps `mvcommon` stdlib-only |

---

## Steps

> Steps below assume **Option A** (sha256sum), **OD-2(a)** (warn and skip), and
> **OD-3(a)** (inline in `main.py`). If the user's answers differ, the executor
> adjusts the implementation accordingly.

---

### Step 1 — [x] [model: haiku] Add `PUSH_VERIFY_REMOTE` constant

**Files:** `main.py`

**Details:**
- Add `PUSH_VERIFY_REMOTE = False` near the other module-level push constants
  (`PARTIAL_SUFFIX`, `MVMETA_SUFFIX`, `REMOTE_ROOT`). One line, a comment noting
  it is gated here until IMP-A5 adds config-file support.
- No logic changes — this step is purely additive.

**Acceptance:** `python -c "import main; assert main.PUSH_VERIFY_REMOTE is False"`
resolves; no other files changed.

---

### Step 2 — [x] [model: sonnet] Implement post-push verification in `cmd_push`

**Files:** `main.py`

**Details:**

#### 2a — Build `_chunk_hashes` before the upload loop

Before `# 3. UPLOAD LOOP`, add a `_chunk_hashes: dict[str, str]` that maps
`local_filename → expected_sha256`. This handles three cases:

```python
_chunk_hashes: dict = {}
if chunk_metadata:
    # New split: just populated above
    _chunk_hashes = {c["filename"]: c["hash"] for c in chunk_metadata
                     if c.get("hash")}
elif "split_info" in library.get(manual_id, {}):
    # Resume (pre-existing _parts/): hashes already in library
    _chunk_hashes = {
        c["filename"]: c["hash"]
        for c in library[manual_id]["split_info"].get("chunks", [])
        if c.get("hash")
    }
# Single-file push: _chunk_hashes stays empty; verification is skipped
```

This dict is available in the closure scope of `_push_and_rename()`.

#### 2b — Add `_verify_chunk_hash()` private function

Add a module-level private helper in `main.py` (above `cmd_push` or near the
other private push helpers):

```python
def _verify_chunk_hash(adb_base, remote_path, safe_path, expected_sha256):
    """Run adb shell sha256sum on the remote file; raise CalledProcessError on mismatch.

    On command-not-found (non-zero exit from sha256sum itself), print a warning
    and return without raising — the push is kept alive (OD-2a).
    """
    try:
        result = subprocess.run(
            adb_base + ["shell", "sha256sum", f"'{safe_path}'"],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError:
        print(f"  ⚠️  sha256sum unavailable on device — remote verification skipped for {os.path.basename(remote_path)}")
        return
    # Format: "<hash>  <path>\n"
    remote_hash = result.stdout.strip().split()[0]
    if remote_hash != expected_sha256:
        raise subprocess.CalledProcessError(
            1, f"hash mismatch for {os.path.basename(remote_path)}"
        )
```

**Why this raises CalledProcessError:** The surrounding `retry()` call is
`retry_on=(subprocess.CalledProcessError,)`. Raising CalledProcessError here
causes C2's retry wrapper to re-run the entire `_push_and_rename()` closure
(push → mv → verify), exactly as required. After exhaustion, the outer
`except subprocess.CalledProcessError` fires `all_success=False; break;
return False` — the failure contract is unchanged.

#### 2c — Call `_verify_chunk_hash()` inside `_push_and_rename()`

Extend the existing `_push_and_rename()` closure with the verification call:

```python
def _push_and_rename():
    subprocess.run(adb_base + ["push", "-p", f, remote_partial_path], check=True)
    subprocess.run(
        adb_base + ["shell", "mv", f"'{safe_partial}'", f"'{safe_final}'"],
        check=True,
    )
    # IMP-C8: post-push remote hash verification (gated on PUSH_VERIFY_REMOTE)
    if PUSH_VERIFY_REMOTE:
        expected = _chunk_hashes.get(local_fname)
        if expected:
            _verify_chunk_hash(adb_base, remote_full_path, safe_final, expected)
```

**Notes:**
- `_chunk_hashes`, `local_fname`, `remote_full_path`, `safe_final`, `adb_base`
  are already in the closure scope — no signature changes.
- `PUSH_VERIFY_REMOTE=False` → the `if` body never executes; happy path is
  byte-for-byte identical to today.
- `PUSH_VERIFY_REMOTE=True` and `expected is None` (single-file push or chunk
  hash not found): verification is skipped silently. The hash lookup failing is
  not a corruption signal — it just means no pre-computed hash is available.
- The `_cleanup_and_log` `on_retry` callback already does
  `adb shell rm '<.partial>'` (no-op since the `.partial` was already mv'd
  to the final name before verification ran). On the next retry attempt, the
  fresh `adb push` pushes to `.partial` and `mv` overwrites the corrupt final
  file — no extra cleanup needed.

**Acceptance:**
- `PUSH_VERIFY_REMOTE=False`: `_verify_chunk_hash` never called; zero extra
  subprocess calls in existing tests (regression verified in step 3).
- `PUSH_VERIFY_REMOTE=True`, hash matches: push succeeds, entry onboarded.
- `PUSH_VERIFY_REMOTE=True`, hash mismatches: CalledProcessError raised inside
  `_push_and_rename()`; C2's retry wrapper fires; on exhaustion `cmd_push`
  returns `False` and entry stays unchanged.

---

### Step 3 — [x] [model: sonnet] Tests + sha256sum in mock_device

**Files:** `tests/conftest.py`, `tests/test_cmd_push_verify.py`

#### 3a — Add sha256sum to `mock_device` in `conftest.py`

The `mock_device` fixture currently handles `adb shell md5sum`. Add an
`elif sub[0] == "sha256sum":` branch using `hashlib.sha256`:

```python
elif sub[0] == "sha256sum":
    path = sub[-1].strip("'")
    p = device_dir / path.lstrip("/")
    if p.exists():
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        res.stdout = f"{h}  {path}\n"
    elif check:
        raise subprocess.CalledProcessError(1, argv)
```

This is a 6-line addition adjacent to the existing `md5sum` block. No other
conftest changes; the sandbox library-binding logic is untouched.

> **Note (multi-candidate interaction):** step 3a is **only required by
> Approach B** below. If the judge selects Approach A (inline recorder), the
> `mock_device` `sha256sum` branch is not needed and should be omitted to keep
> the change surgical. The two approaches are described in the Multi-candidate
> subsection at the end of this step — the executor for each candidate should
> include or omit 3a according to its approach.

#### 3b — New test file `tests/test_cmd_push_verify.py`

Use the `sandbox` fixture (from conftest) for library isolation. For adb
interaction, define a local `FakeAdbVerify` recorder (following the pattern of
`FakeAdb` in `test_cmd_push_retry.py`) that:
- Copies files on `adb push` (or records without actual copy, per test need)
- Returns the correct sha256sum of the "device" copy on `adb shell sha256sum`
- Can be configured to: return a correct hash, return a wrong hash, or raise
  CalledProcessError on sha256sum (command unavailable)

> **Constraints (mandatory for this step):**
> - Never touch real `C:\Media` files or real `library_*.json`. Use the
>   `sandbox` fixture (it patches BOTH `mvcommon.LIBRARY_*` AND
>   `main.LIBRARY_*`) — do not DIY the library redirect.
> - Run `pytest -q` and fix failures before marking the step done.

**Tests:**

**(a) `PUSH_VERIFY_REMOTE=False` — zero extra subprocess calls (regression guard)**
- Monkeypatch `main.PUSH_VERIFY_REMOTE = False`
- Run `cmd_push` on a split entry
- Assert no `sha256sum` call appears in recorded adb calls
- Asserts the existing behaviour is byte-identical (no regression)

**(b) Hash matches — push succeeds, entry onboarded**
- Monkeypatch `main.PUSH_VERIFY_REMOTE = True`
- `FakeAdbVerify` returns the correct sha256 for the pushed chunk
- `cmd_push` returns `True`; entry `uploaded=True`, `status="onboarded"`
- `sha256sum` was called exactly once per chunk

**(c) Hash mismatches once then matches — C2 retry fires**
- Monkeypatch `main.PUSH_VERIFY_REMOTE = True`
- `FakeAdbVerify` returns a wrong hash on the first sha256sum call, correct
  on the second (models a transient corruption that resolves on re-push)
- Monkeypatch `mvcommon.time.sleep` to no-op (instant retry)
- `cmd_push` returns `True`; entry onboarded
- sha256sum was called twice; `adb shell rm '<...>.partial'` was issued once
  by the `on_retry` callback (verifying the existing C2 cleanup hook fires)

**(d) Hash mismatches all 3 retries — failure contract unchanged**
- Monkeypatch `main.PUSH_VERIFY_REMOTE = True`
- `FakeAdbVerify` always returns a wrong hash
- `cmd_push` returns `False` (same failure contract as a failed push)
- Entry `uploaded` stays `False`; `_parts/` left populated
- Exactly 3 sha256sum calls (one per retry attempt)

**(e) sha256sum command unavailable — warn and skip, push succeeds**
- Monkeypatch `main.PUSH_VERIFY_REMOTE = True`
- `FakeAdbVerify` raises `CalledProcessError` on sha256sum
- `cmd_push` returns `True`; entry onboarded
- Warning line "sha256sum unavailable" printed to stdout (assert via `capsys`)
- No retry fired (the unavailable-command path is NOT retried)

**Acceptance:**
`pytest tests/test_cmd_push_verify.py -q` green; all five scenarios above pass.
Existing tests still green: `pytest -q` (full suite).

**Multi-candidate:** [candidates: 2] — this step runs as a multi-candidate step.
The two approaches differ in **test infrastructure strategy**, not in the five
scenarios being asserted (a–e are identical for both candidates; the contract is
fixed). This is a genuine architectural fork with non-obvious tradeoffs — a fast
self-contained recorder versus a real-bytes round-trip through the shared
fixture — and seeing both side by side lets the judge pick the better testing
foundation that future verify tests will inherit. The other steps are
single-executor (see Multi-candidate summary).

- **A — inline `FakeAdbVerify` recorder (no filesystem).**
  Build a standalone recorder class inside `tests/test_cmd_push_verify.py`,
  mirroring the `FakeAdb` pattern in `test_cmd_push_retry.py`: it records every
  adb argv and, on `adb shell sha256sum`, returns a configurable hash (correct /
  wrong-once-then-correct / always-wrong) or raises `CalledProcessError`
  (command unavailable). No real bytes are copied; the "expected" hash is
  whatever the recorder is told to emit, and the stored chunk hash is seeded to
  match. Uses only the `sandbox` fixture for library isolation. Under this
  approach the `conftest.py` `sha256sum` branch (3a) is **not added** — keep the
  change confined to the new test file.
  - Pros: fast, fully deterministic, trivial fault injection (wrong/missing hash
    is a constructor flag), zero shared-fixture surface area, self-contained in
    one file, mirrors an existing in-repo pattern.
  - Cons: does not prove a real round-trip (bytes-pushed vs bytes-hashed is never
    actually exercised); the recorder's sha256sum output format can drift from
    real adb output.

- **B — extend the stateful `mock_device` conftest fixture.**
  Add the `elif sub[0] == "sha256sum":` branch to `mock_device` (step 3a) that
  computes `hashlib.sha256` over the real file copied into `device_dir`, then
  write the tests against `mock_device` directly. Fault injection is done by
  corrupting the on-device bytes so the real sha256 genuinely differs (e.g.
  mutate the device-side file before verification, or intercept the push to
  write divergent bytes); "command unavailable" is simulated by raising from /
  disabling the branch. Uses `sandbox` + `mock_device`.
  - Pros: real round-trip integrity (the hash is computed over bytes that were
    actually "pushed"); reuses the shared fixture so future verify tests inherit
    sha256sum support; closer to integration behaviour.
  - Cons: more setup per test; fault injection is more indirect (corrupt real
    bytes rather than flip a flag); couples C8 tests to `mock_device` internals;
    the conftest change touches a shared fixture other tests depend on (wider
    blast radius).

**Judge criteria (ranked, most important first):**
1. **Correctness / completeness:** all five scenarios (a–e) pass and assert the
   right contract — return value, library entry state (`uploaded`, `status`),
   sha256sum call count, retry + `rm '.partial'` behaviour, and warn-and-skip on
   unavailable.
2. **Fault-injection clarity:** how readable and unambiguous "return a wrong
   hash" and "command unavailable" are in each scenario.
3. **Regression strength:** does scenario (a) genuinely prove zero extra
   subprocess calls when `PUSH_VERIFY_REMOTE=False`.
4. **Blast radius / maintainability:** shared-fixture (`conftest.py`) changes
   weigh against a candidate unless clean and well-scoped; a self-contained
   recorder weighs for a candidate unless it duplicates too much of `FakeAdb`.
5. **Fidelity:** does the test exercise a real push-vs-hash round trip or only a
   recorder's canned response.

---

### Step 4 — [model: haiku] Mark IMP-C8 done; update ARCHITECTURE.md

**Files:** `improvements_tierC.md`, `ARCHITECTURE.md`

**Details:**
- In `improvements_tierC.md`, change IMP-C8's `Status: pending` to
  `Status: done (feature/post_push_verify, PR to main <DATE>)`, mirroring the
  wording style of IMP-C9 and IMP-C2.
- In `ARCHITECTURE.md`, add one to two sentences in the `cmd_push` section
  (§7 or §7.5) noting that with `PUSH_VERIFY_REMOTE=True`, `sha256sum` is run
  on the device after each push+mv, compared to the stored chunk hash, with a
  `CalledProcessError` on mismatch that feeds back into the C2 retry wrapper.
  Note the default is `False` and configurability is IMP-A5. Doc-only step.

**Acceptance:** IMP-C8 shows `done`; ARCHITECTURE.md mentions the verification
step and the `PUSH_VERIFY_REMOTE` flag.

---

### Step 5 — [model: haiku] Fill completion report; restore root PLAN.md to auto-rollback; update tracker

**Files:** `docs/feature-auto-rollback/C8-post-push-verify/C8-post-push-verify.md`,
`docs/feature-auto-rollback/_TRACKER.md`,
`/PLAN.md`

**Details:**

1. Fill the "Completion report" section in `C8-post-push-verify.md` (Branch, PR,
   Merged commit, Files changed, Tests added, Manual test commands, Open decisions
   resolved, Notes/surprises, Follow-ups).

2. In `docs/feature-auto-rollback/_TRACKER.md`, mark C8 done:
   - Change `| 6 | [[C8-post-push-verify]] … | not-started |` → `| 6 | … | done |`
   - Tick the checklist entry:
     `- [ ] C8 — post-push remote md5sum verify`  →
     `- [x] C8 — post-push remote verify *(done — feature/post_push_verify, PR to main <DATE>)*`

3. **Restore `/PLAN.md` (repo root) to the auto-rollback plan.** The root
   `PLAN.md` is the live working copy the orchestrator uses for the NEXT task.
   Now that C8 is complete, restore it to the auto-rollback draft plan:
   - Copy `docs/feature-auto-rollback/PLAN.md` → `/PLAN.md`
   The auto-rollback plan's status block at the top already records which
   prerequisites are done; the tracker update above is the authoritative status
   record for that plan. No edits to the auto-rollback PLAN.md body are needed —
   just restore it as the root copy.

**Acceptance:**
- Completion report populated in `C8-post-push-verify.md`.
- `_TRACKER.md` shows C8 as `done` with branch/PR note.
- `/PLAN.md` at root matches `docs/feature-auto-rollback/PLAN.md` (auto-rollback plan), NOT the C8 plan.

---

## Multi-candidate summary

| Step | Mode | Reasoning |
|------|------|-----------|
| 1 — `PUSH_VERIFY_REMOTE` constant | **Single** | One-line additive constant; no design choice; every executor writes identical code. |
| 2 — verification logic in `cmd_push` | **Single** | The spec is fully prescriptive: `_chunk_hashes` is a `dict[filename→hash]`, `_verify_chunk_hash()` is a single `subprocess.run` + parse + compare-or-raise, and the hard requirement to raise `CalledProcessError` (to feed C2's `retry`) pins the control flow. Helper location is fixed by OD-3, lookup structure by 2a. Two executors would converge — no genuine fork. |
| 3 — tests + sha256sum fixture | **Multi (2)** | Genuine architectural fork in test infrastructure: a self-contained inline `FakeAdbVerify` recorder (fast, deterministic, no conftest change) vs. extending the stateful `mock_device` fixture for a real-bytes round trip (higher fidelity, wider blast radius). Tradeoffs are non-obvious and the choice sets the pattern future verify tests inherit, so side-by-side judging adds real value. |
| 4 — docs / status update | **Single** | Doc-only edits to `improvements_tierC.md` and `ARCHITECTURE.md`; mechanical, no design space. |
| 5 — completion report + restore root PLAN.md | **Single** | Doc-only: fill completion report, tick C8 done in `_TRACKER.md`, restore `/PLAN.md` to the auto-rollback plan so the next session picks up the right live working copy. |

Net: 1 of 5 steps is multi-candidate (Step 3) — within the expected 0–2 range
for a plan this size. Steps 1, 2, 4, 5 are single-executor.

---

## Risks and edge cases

**Hash algorithm mismatch (highest risk).** `split_info.chunks[i].hash` is SHA-256.
If the device-side command is `md5sum`, the hashes will NEVER match. OD-1 must be
answered before implementation. Guard: the test suite proves round-trip (push known
bytes → sha256sum via mock → compare to pre-computed sha256).

**Single-file push (no split_info).** `_chunk_hashes` will be empty; `expected` is
`None`; verification is skipped. This is correct — there is no pre-computed local
hash to compare against for single-file pushes. The test suite covers this implicitly
via the `PUSH_VERIFY_REMOTE=False` regression guard (single-file is the common
non-split path).

**Resume case (pre-existing `_parts/` folder).** `chunk_metadata` is empty but
`library[manual_id]["split_info"]["chunks"]` contains the hashes from the original
split. The `_chunk_hashes` builder (Step 2a) covers this with the `elif` branch.

**Retry on mismatch: remote file state.** After a mismatch, the file is at the final
remote path (the `.partial` was already mv'd). On retry, `adb push` re-uploads to
`.partial` and `adb shell mv` overwrites the corrupt final file. `adb shell mv src dst`
when dst exists overwrites on Android/Linux. The existing `on_retry` `rm .partial`
is a no-op (the `.partial` is gone) — `check=False` keeps it safe.

**Hash command unavailable is not retried.** `_verify_chunk_hash()` catches the
`CalledProcessError` from the hash command itself and returns (warn+skip) BEFORE
the mismatch `raise`. This means "command unavailable" does NOT trigger a retry —
correct, since the command will still be unavailable on every retry.

**`capture_output` in mock.** The `mock_device` fixture's `fake_run` accepts
`**kwargs`, so `capture_output=True, text=True` passes through silently. `res.stdout`
is already set as a string. No fixture changes needed beyond the sha256sum handler.

**Retry message misleading for mismatch.** The `_cleanup_and_log` on_retry prints
"ADB push failed". For a hash mismatch the push itself succeeded. Executor may update
the print to "ADB push/verify failed" to be more accurate without changing behaviour.

**`check=True` on sha256sum call.** This raises CalledProcessError when the command
is not found (exit code 127) or when the file doesn't exist (exit code 1). Both are
caught by `_verify_chunk_hash()`'s `except CalledProcessError:` and result in a
warn+skip, not a spurious retry. This is correct.

---

## Verification

Run from the repo root after all steps complete:

```powershell
pytest -q                                   # full suite must be green
pytest tests/test_cmd_push_verify.py -q     # C8-specific: all 5 scenarios
pytest tests/test_cmd_push_retry.py -q      # C2 regression: unchanged
```

---

## Manual test commands

```powershell
# 1. Smoke: PUSH_VERIFY_REMOTE=False (default) — no behaviour change
python main.py push <mov-id> SIZE_GB 10
# Expected: works as before, no sha256sum lines in output.

# 2. Enable verification by temporarily editing main.py:
#    PUSH_VERIFY_REMOTE = True
# Then push a small entry:
python main.py push <small-mov-id>
# Expected: push completes normally; no extra output on a clean cable.

# 3. Simulate a mismatch (manual): after the push, corrupt the remote file via
#    adb shell, then re-run push on the _parts/ folder chunks.
#    The push should print ⏳ Retry lines and either self-heal or return ❌ FAILED.
```

---

## Branch / PR / end matter

- **Branch:** `feature/post_push_verify`, cut from `origin/main` (A1 = `1aac738`,
  C2 = `cf79684`, G1 = `8c12680` confirmed merged).
- **PR title:** `Feature: post-push remote hash verification — IMP-C8`
- **PR body structure:** auto-generated Claude Code summary FIRST, then
  `## Original task prompt` with the verbatim task prompt, then the
  `🤖 Generated with Claude Code` trailer.

---

## Out of scope

- Configurability of `PUSH_VERIFY_REMOTE` via `mvconfig.json` (IMP-A5).
- Verification for the `cmd_replace` dummy push or `cmd_restore` fetch (C11
  handles restore-side integrity; C8 is push-only).
- The `cmd_set_uploaded` ADB verification (IMP-C7 — separate command).
- Anything under `archive/`.
- Pre-G1 push code path (does not exist in `origin/main`).
- Multi-device support (`adb -s <serial>` pinning is IMP-C4 and already
  threaded through `adb_base` — C8 reuses `adb_base` as-is).
