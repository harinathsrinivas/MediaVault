# Candidate B Self-Critique

## Approach taken
Extended the shared stateful `mock_device` conftest fixture with an
`elif sub[0] == "sha256sum":` branch that computes `hashlib.sha256` over the real
file copied into `device_dir`, then wrote the five tests against `mock_device` so the
hash is computed over bytes that were genuinely "pushed". Fault injection is real:
scenario (d) seeds a deliberately wrong stored hash; scenario (c) corrupts the
on-device bytes after the first push (then the clean retry re-pushes and matches);
scenario (e) makes the sha256sum branch raise. The tests run against a split entry in
the resume state so `_chunk_hashes` is sourced from `split_info.chunks`.

## Strengths
- Real push-vs-hash round trip: scenario (b) asserts the device file's actual sha256
  equals the stored hash, proving bytes-pushed == bytes-hashed (true fidelity).
- Reuses the shared fixture, so future C8/verify tests inherit sha256sum support for free.
- Fault injection in (c) is genuine wire corruption (mutate device bytes), closest to
  the real failure mode C8 defends against.
- Full suite stays green (51 passed) after the conftest change.

## Weaknesses
- **Touches a shared fixture** (`conftest.py`) other suites depend on — wider blast
  radius. Beyond the additive `sha256sum` branch, it also had to change the `mv`
  handler from `Path.rename` to `Path.replace`: the original mock `mv` does NOT
  overwrite an existing destination on Windows, so the C8 retry-after-mismatch path
  (final file already present) raised `WinError 183` and masked the verify retry as an
  mv error. The fix is more faithful to Android `mv` (which overwrites), but it is a
  behavioural change to a fixture shared by the G1/C2 push tests.
- More setup per test; fault injection is indirect (corrupt bytes / wrap subprocess.run
  with a counter) rather than a single constructor flag.
- The deep `sdcard/Media/../Users/...` device path (sandbox media dir lives on a temp
  drive, so `os.path.relpath` yields `..`) made the obvious `rglob(CHUNK_NAME)` miss;
  the round-trip assertion needed a tail-glob workaround — a sign of `mock_device`
  internals leaking into the test.

## Tests run
- `pytest tests/test_cmd_push_verify.py -q` -> 5 passed
- `pytest -q` (full suite) -> 51 passed (46 baseline + 5 new), no regressions

## Confidence
medium-high — the five scenarios pass and the real round trip is a genuine fidelity
win, but the approach required a behavioural change to a shared fixture (`mv`
overwrite) that was not anticipated in the plan's 3a, plus a path-glob workaround.
Both are evidence of the wider blast radius this approach carries.
