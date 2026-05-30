# Step 3 Judge Decision — C8 post-push verify tests

**Winner: Candidate A** (inline `FakeAdbVerify` recorder, no conftest change).

Both candidates were evaluated blind against the plan's ranked judge criteria. Both
implement the identical five scenarios (a–e) and both pass `pytest tests/test_cmd_push_verify.py -q`
(5 passed) and the full suite (51 passed).

## Scoring against ranked criteria

1. **Correctness / completeness — TIE.** Both pass all five scenarios and assert the
   full contract: return value, library `uploaded`/`status`, exact sha256sum call count,
   the C2 retry + `rm '<...>.partial'` hook, and warn-and-skip on unavailable.

2. **Fault-injection clarity — A.** A selects the fault with a single constructor flag
   (`sha256_mode="wrong" | "wrong_then_correct" | "unavailable"`), readable at a glance.
   B injects faults indirectly: real on-device byte corruption (c), a deliberately wrong
   stored hash (d), and a wrapped `subprocess.run` raising on sha256sum (e) — genuine but
   harder to read and reason about.

3. **Regression strength (scenario a) — A (slight).** Both literally assert zero
   sha256sum calls when `PUSH_VERIFY_REMOTE=False` (A: `fake.sha256_calls() == []`;
   B: `rec["sha256"] == []`). A's is simpler with no wrapper indirection.

4. **Blast radius / maintainability — A (decisive).** A is confined to one new test file,
   makes zero shared-fixture changes, and mirrors the existing in-repo `FakeAdb` pattern.
   B modifies the shared `tests/conftest.py`: not only the planned additive `sha256sum`
   branch (3a) but ALSO a behavioural change to the `mv` handler (`Path.rename` ->
   `Path.replace`) — the original mock `mv` does not overwrite an existing destination on
   Windows, so B's retry-after-mismatch path raised `WinError 183` and masked the verify
   retry as an mv error until the fixture was changed. That `mv` handler is shared by the
   G1 and C2 push suites, so B widens the blast radius beyond what the plan's 3a
   anticipated. This is exactly the maintainability risk the plan flagged for Approach B.

5. **Fidelity — B.** B exercises a real push-vs-hash round trip (the device file's actual
   sha256 is compared to the stored hash); A uses a canned recorder response. This is the
   one criterion B wins, and it is the lowest-ranked.

## Rationale

The criteria are ranked most-important-first. A ties #1, wins #2, #3 (slightly), and #4
(decisively); B wins only the lowest-ranked #5. The #4 finding is load-bearing: B's
approach required an *unplanned* behavioural change to a fixture shared by other suites,
realizing the precise blast-radius risk the plan called out. A's self-contained recorder
sets a cleaner, lower-risk pattern for the verify tests that future steps will inherit,
at the cost of fidelity — an acceptable tradeoff given `_verify_chunk_hash`'s parser is
simple and the round trip is already implicitly covered by the existing `mock_device`
push tests.

**Selected: Candidate A.**
