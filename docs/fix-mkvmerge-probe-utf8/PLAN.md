# Task: Make the `mkvmerge -J` unsplittable-track probe UTF-8-safe on Windows

Suggested branch: fix/mkvmerge-j-utf8-decode  (already created from main, 562fb4a)

## Context

`find_unsplittable_tracks()` in `main.py` runs the IMP-C20 pre-flight probe
`mkvmerge -J <file>` with `capture_output=True, text=True` but **no `encoding`**.
On Windows `text=True` decodes subprocess stdout with the locale default
`cp1252`, **strict**. `mkvmerge -J` emits valid UTF-8 JSON that includes a
per-track `track_name`. For a release with an Icelandic subtitle track stored in
NFC-composed form (`"I \xcc\x81 slenska"` = "Íslenska"), the UTF-8 combining-acute
byte is `0x81` — an **undefined byte in cp1252** — so the decode raises
`UnicodeDecodeError` inside subprocess's background `_readerthread`. Because that
exception is raised on the reader thread, it is NOT caught by the enclosing
`try/except Exception`, crashing the thread, printing a spurious traceback, and
degrading the probe every run.

This is an edge case of the existing `docs/edge-case-unsplittable-tracks/` feature.
It was encountered live on `prep_push_rep_enrich` for "Thor: Love and Thunder"
(51 GB REMUX) — the exact release that the probe is meant to *protect* (refuse a
file mkvmerge can't split BEFORE prep burns the deep scan + whole-file hash).

## Goal

`find_unsplittable_tracks` no longer raises a `UnicodeDecodeError` on its reader
thread when the `-J` JSON carries a non-cp1252 UTF-8 track name; the probe
correctly parses the JSON and returns the FLAC verdict (or `[]`). The IMP-C20 gate
is restored to its intended behavior for files with non-ASCII track metadata.

## Files affected

- `main.py` — `find_unsplittable_tracks()`: add `encoding="utf-8"` (+ `errors="replace"` safety) to its single `subprocess.run` call (lines 261-262). No other code path touched.
- `tests/test_unsplittable_preflight.py` — add one regression test asserting the probe passes `encoding="utf-8"` to `subprocess.run` (so a future edit can't silently reintroduce the cp1252 default); extend the existing `_stub_identify` helper to optionally record kwargs.
- `docs/fix-mkvmerge-probe-utf8/PLAN.md` — this plan (tracked canonical copy).
- `/PLAN.md` (repo root) — identical plan (gitignored live copy; NOT committed).

## Approach

The reader-thread decode uses the subprocess `text`/`universal_newlines` machinery,
whose codec is chosen by the `encoding` kwarg (or `locale.getpreferredencoding()`
on Windows = cp1252). Passing `encoding="utf-8"` overrides that choice so the
reader thread decodes the UTF-8 mkvmerge JSON correctly. `errors="replace"` is a
belt-and-suspenders guard so a *pathological* non-UTF-8 byte can never crash the
reader thread again — it would substitute `U+FFFD` and let `json.loads` (or the
`except` → `[]`) handle the rest, exactly as the documented "probe failure never
blocks an archive" contract intends. This is a one-kwarg change; the surrounding
`try/except Exception: return []` already does the right thing once the decode
succeeds.

The other `text=True` subprocess calls (split ~382, merge ~413, `--version` ~441,
ffmpeg dummy ~591) emit ASCII-only output and are deliberately out of scope (see
Out of scope).

## Steps

- [ ] 1. [model: sonnet] [effort: low] Add `encoding="utf-8", errors="replace"` to the `-J` probe.
  - Files: `main.py` (lines 261-262)
  - Details: Change
    `r = subprocess.run([MKVMERGE_PATH, "-J", input_path], capture_output=True, text=True, check=True)`
    to
    `r = subprocess.run([MKVMERGE_PATH, "-J", input_path], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)`.
    Touch nothing else. Do NOT reorder/reformat the call.
  - Acceptance: `find_unsplittable_tracks` calls `subprocess.run` with `encoding=="utf-8"` and `errors=="replace"`.

- [ ] 2. [model: sonnet] [effort: medium] Add a regression test locking the encoding kwarg.
  - Files: `tests/test_unsplittable_preflight.py`
  - Details: Extend the `_stub_identify` helper's `_run` stub to also record `kwargs`
    into an optional list, then add a test like
    `test_probe_passes_utf8_encoding` that calls `find_unsplittable_tracks` and
    asserts the recorded `kwargs.get("encoding") == "utf-8"` (and `errors == "replace"`).
    Keep it in the existing file, matching house style and docstring tone. Never
    touch real `C:\Media` files or real `library_*.json`.
  - Acceptance: the new test fails on the pre-fix code (would assert encoding is
    utf-8 when it is absent → `None`), passes after step 1. Run `pytest -q` on the
    file and fix failures before marking done.

- [x] 3. Decision recorded: do NOT overwrite the root `/PLAN.md`.
  - The repo-root `/PLAN.md` is the **gitignored live working copy of the in-flight
    `feature/imp_u6_provider_tokens` run** (852-line imp_u6 plan). Per the explicit
    user instruction "we should not be touching that [other branch]", this fix's plan
    is carried only in the tracked `docs/fix-mkvmerge-probe-utf8/PLAN.md` (the file
    that ships with this branch). The root live copy is left untouched for imp_u6.

## Risks and edge cases

- **UTF-8 vs cp1252 is the whole class, but mkvmerge `-J` is definitively UTF-8**
  (its JSON documents use multi-byte UTF-8). `encoding="utf-8"` is therefore
  correct, not a guess. `errors="replace"` covers any pathological non-UTF-8 byte.
- **The reader-thread exception is outside all `try/except` in the caller.** The
  fix removes its trigger. We do NOT add a broad subprocess-output wrapper, because
  (a) `encoding` is the minimal correct fix and (b) silently swallowing decode
  errors elsewhere would mask real corruption. The single targeted kwarg is the
  surgical change.
- **No behavior change for ASCII-only files** (the overwhelmingly common case) —
  UTF-8 and cp1252 agree on all ASCII bytes, so the probe output is byte-identical.
- **Test-harness risk:** the existing `_stub_identify` `_run(cmd, **kwargs)` already
  tolerates a new kwarg (it accepts `**kwargs`), so step 1 cannot break the existing
  10 tests. Confirmed by baseline: all pass pre-change.

## Verification

1. `python -m pytest tests/test_unsplittable_preflight.py -q` — new + existing 11 tests pass.
2. `python -m pytest tests/smoke -q` **— mandatory SMOKE-GATE: `main.py` is touched. This is the final gate.** (Baseline on the clean branch: 80 passed.)

## Out of scope

- **The `[WinError 5] Access is denied` on `.mediavault_txn.json.tmp -> .mediavault_txn.json`**
  (a transient Windows file lock on the rollback journal during `_flush()`,
  documented in `docs/edge-case-replace-ponr-journal-lock/` and IMP-R10) is
  **NOT implemented here**. It is a separate root cause, sits on the **change-gated**
  rollback/journal-durability surface, and the user chose *report-only* for it this
  session (recommend handling it in its own change-gated task).
- **The other `text=True` subprocess calls** (split/merge/`--version`/ffmpeg dummy)
  are NOT touched — they emit ASCII-only output and show no live failure. Decay note:
  if a future mkvmerge/ffmpeg emits non-ASCII on those streams, they would warrant
  the same `encoding="utf-8"` treatment; out of scope today (Simplicity First).
- No changes to rollback, PONR placement, journal format/durability, `recover_journal`,
  entry-type schemas, or shared data contracts — therefore **no Consumer Impact
  Analysis section** is required (no `library` field/key/type/`status`/id shape changes).

## Stash note (repo hygiene)

Before this branch was created, the in-flight uncommitted `main.py` doc changes on
`feature/imp_u6_provider_tokens` were stashed to keep the working tree clean for the
branch switch:
`git stash` → `On feature/imp_u6_provider_tokens: imp_u6 in-flight doc/token-comment changes (WIP)`
A byte-identical backup patch was also written to
`%TEMP%\imp_u6_uncommitted_doc_changes.patch` (17,114 bytes).
When returning to `feature/imp_u6_provider_tokens`, run `git stash pop` to restore
that work (and this branch must NOT be merged onto it).