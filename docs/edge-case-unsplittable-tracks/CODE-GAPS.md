# Code Gaps — what the unsplittable-track incident exposed

> **Status as of 2026-08-24 — registered, two of three shipped.**
>
> | Gap | Code | State |
> |---|---|---|
> | 1 — split failure undiagnosable | **IMP-C19** | ✅ done — `1af16a3` |
> | 2 — no unsplittable-track preflight | **IMP-C20** | ✅ done — `e2b799c` |
> | 3 — assisted remux | **IMP-C20**'s follow-on | ⏸️ open, deliberately **opt-in only** (see below) |
>
> A fourth, unrelated bug surfaced during the same archival run — a transient lock
> on the rollback journal during `cmd_replace`'s point-of-no-return write — and is
> tracked separately as **IMP-R10**. It is **not fixed** and its fix is
> change-gated: [`../edge-case-replace-ponr-journal-lock/README.md`](../edge-case-replace-ponr-journal-lock/README.md).
>
> Written 2026-08-24 from the `mov-kor-2003-ataleoftwosisters` incident.
> Background: [`ISSUE-flac-split-failure.md`](ISSUE-flac-split-failure.md) ·
> [`CODEC-SPLIT-MATRIX.md`](CODEC-SPLIT-MATRIX.md) ·
> [`RUNBOOK-remux-before-split.md`](RUNBOOK-remux-before-split.md)

---

## Gap 1 — the split failure was undiagnosable — ✅ IMP-C19 (`1af16a3`)

**Severity was: high. Effort: two lines.** This is the gap that turned a one-line
explanation into a multi-hour investigation.

**What was wrong.** `mkvmerge` writes its `Error:` lines to **stdout**, not stderr.
`split_video_file` sent stdout to `DEVNULL` and captured a stderr that was then
never read:

```python
# as it stood before IMP-C19
        # Added stderr capture to output real error messages
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        ...
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running mkvmerge for splitting: {e}");
        return []
```

Three defects stacked: `stdout=subprocess.DEVNULL` discarded the only stream
carrying the diagnosis; the handler printed the `CalledProcessError` repr (argv +
exit code) and never touched the captured `e.stderr`; and the comment claimed the
opposite of what the code did. Net effect for the operator:
`returned non-zero exit status 2` and nothing else. The message thrown away was
`Error: The track ID 4 from the file '…' cannot be split. Splitting tracks of this type is not supported.`

`merge_video_files` had the identical defect and it was worse there — stdout
discarded and stderr not even captured. A merge failure happens during
**restore**, i.e. when the local master is already a dummy and the chunks are the
only copy. That is the single worst moment in the system to be blind.

**What shipped.** Both call sites now run mkvmerge with
`stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True` and, on
`CalledProcessError`, hand the exception to the new `_print_mkvmerge_failure(e)`
helper (`main.py:291`), called at `main.py:388` (split) and `main.py:418`
(merge). The helper prints every `Error:`/`Warning:` line found in either stream
and falls back to the last three non-blank lines when mkvmerge died without one —
which is what the libfmt exit-3 crash does (`terminate called … format_error`).
Bytes streams are decoded defensively so reporting can never raise on top of the
failure it is reporting. Happy paths are untouched: nothing extra is printed when
mkvmerge exits 0.

**Tests:** `tests/test_mkvmerge_error_surfacing.py` — 5 cases, including the
libfmt tail fallback and the bytes-stream guard.

---

## Gap 2 — no preflight for unsplittable tracks — ✅ IMP-C20 (`e2b799c`)

**Severity was: medium. Effort: small.**

**What was wrong.** Nothing checked whether the source *could* be split before the
pipeline committed to splitting it. The failure therefore landed in `push`, after
`prep` had already spent a deep tech-spec scan and a whole-file SHA-256 on a 62 GB
file — all of which auto-rollback then correctly discarded.

**What shipped.** A conservative registry and a header-only probe:

- `UNSPLITTABLE_CODEC_IDS = {"A_FLAC"}` (`main.py:249`) — **measured entries only**.
  A false positive would block a legitimate archive, whereas a miss now merely
  costs the legible post-IMP-C19 error at split time. TrueHD is suspected but
  untested and deliberately stays out until measured
  ([`CODEC-SPLIT-MATRIX.md`](CODEC-SPLIT-MATRIX.md) §1).
- `find_unsplittable_tracks(input_path)` (`main.py:252`) — runs `mkvmerge -J` and
  returns `[(track_id, codec_id, language), …]`. Returns `[]` on **any** probe
  failure (missing binary, unreadable file, unparseable JSON): a probe that cannot
  run must never block an archive on its own.
- The call site (`main.py:4735`) sits immediately before `> ✂️ Splitting...`,
  alongside the existing `_free_space_ok` preflight (`main.py:4715`) — read-only,
  **before** any `makedirs` or journal record, so the abort is a clean early return
  with nothing to roll back. The tests assert zero journal records at abort. The
  resume branch never reaches it, so a pre-existing `_parts/` is unaffected.

What the operator now sees instead of a bare exit code:

```
❌ Cannot split mov-kor-2003-ataleoftwosisters — mkvmerge cannot split these tracks:
     • track 4 (A_FLAC, ita)
   Remux the track to a splittable codec first — WavPack is lossless
   and splits fine; dropping the track also works if you don't want it.
   Runbook: docs/edge-case-unsplittable-tracks/RUNBOOK-remux-before-split.md
```

It names the track and hands over the procedure. It does **not** convert or drop
anything — see Gap 3.

**Tests:** `tests/test_unsplittable_preflight.py` — 7 cases, including the
clean-early-return assertions and a guard that the registry stays conservative.

**Closed by the follow-up (`1b1a899`).** The first IMP-C20 commit gated only at
`push`, which still let `prep_push_rep` spend its deep scan and whole-file hash
first — the exact waste this gap was about. Both auto-pilots now run
`refuse_if_unsplittable()` before their prep leg (`main.py:7181` and
`main.py:7244`), the season variant across every episode in the folder, and
`cmd_push` keeps its check (`main.py:4735`) as the backstop for a plain `push`
on an already-prepped entry. Both gates no-op when no split was requested.

---

## Gap 3 — assisted remux — ✅ RESOLVED as a separate manual tool (IMP-C21)

**Status: shipped 2026-08-25 as `tools/remux_unsplittable.py`, on `feature/imp_c21_remux_unsplittable`.
Resolved *more* conservatively than the opt-in flag sketched below.**

> The fix did not land inside the pipeline at all. Per the user's 2026-08-25
> instruction — *"keep it separate. do not call it automatically if any issues.
> I will do that manually after checking each case error by error"* — it is a
> standalone script MediaVault never invokes:
>
> - not imported by `main.py` / `mainfetch.py` / `mvcommon.py`, not wired to any
>   command, not offered as an automatic remedy. `tests/test_remux_unsplittable.py`
>   asserts none of those modules even mention it, so the automatic-conversion line
>   cannot be crossed by accident later.
> - **dry run unless `--run`** — prints the offending track, the plan, the exact
>   ffmpeg argv and the disk arithmetic, then stops.
> - never overwrites, never deletes; the delete/rename swap stays with the operator.
> - computes the `-c:a:N` audio index instead of assuming it (on the incident file
>   the FLAC track is overall stream 4 but audio index 3 — the overall index would
>   have hit a subtitle, and `1` the DTS-HD MA main track).
> - verifies stream count, duration drift and the Dolby Vision configuration record;
>   `--verify-streams` adds per-stream checksums, comparing the converted track as
>   decoded PCM.
> - `wavpack` (default) / `pcm` / `drop`. **No lossy targets** — it will not quietly
>   degrade audio.
>
> **The `push <id> --remux-unsplittable=wavpack` flag below was NOT built**, and on
> reflection should not be without a fresh decision: a flag on `push` still puts an
> irreversible codec change one typo away from a long unattended run. The separate
> tool keeps the decision, the inspection and the execution in one deliberate place.

The original framing of this gap — an *automatic* remux triggered by detection —
was rejected outright on 2026-08-24. Retained below for the record, because the
design questions it raises still apply to anything that ever moves this in-pipeline.

### Original sketch (not built)

> ⚠️ **User decision (2026-08-24): MediaVault must never convert or drop a track on
> its own.** The original framing of this gap was an *automatic* remux triggered by
> detection. That is rejected. Changing a track's codec is a permanent, irreversible
> quality decision about the only surviving copy of the file, and it stays a human
> decision about *what* and *when*.
>
> If this is ever built, it is an **explicit per-invocation opt-in** the operator
> types — e.g. `push <id> SIZE_MB 6000 --remux-unsplittable=wavpack` — with no
> default-on behaviour, no config flag that silently arms it, and no fallback path
> that reaches for it when a split fails. Absent the flag, the correct behaviour is
> what IMP-C20 now ships: stop, name the track, print the runbook, and let the
> operator decide.

Scoped that way, the feature is: when the operator passes the flag, transcode
**only** the named track into a temp file, split that, and record the substitution
in `split_info`.

**Open design questions — none of these should be decided by whoever picks this
up alone:**

- **Codec policy must be configurable.** WavPack is the right default (lossless,
  splittable, ~+10% on the affected track) but PCM and "drop the track" are both
  legitimate for different files. This belongs in `mvconfig.json`, not hardcoded.
- **Scratch space.** Auto-remux needs 1X *on top of* the split's 1X. On the
  incident machine that was ~125 GB against 70.5 GB free on the only volume with
  room — **it would not have fit.** The feature must preflight the combined
  requirement and fail cleanly rather than dying mid-remux, and it should be able
  to route the intermediate to `temp_dir`.
- **Provenance is mandatory.** This changes what "the archived master" *is* — the
  restored file will differ from the original in track N's codec. `split_info`
  must record the substitution (original codec, new codec, track id) so a later
  restore or `integrity` run reads the difference as *intended*, not as
  corruption. Without this the feature silently undermines the verification
  story.

**Process:** this touches the split/restore contract, so per
[`CLAUDE.md`](../../CLAUDE.md) it wants a `PLAN.md` through the agent pipeline, a
`PRIORITY.md` band entry and a tier-file task — not a freelance patch.

---

## Applies to all three

- **Smoke gate.** `pytest tests/smoke -q` must be green before any of these
  ship, and before committing any code-touching step. IMP-C19 and IMP-C20 each
  cleared it (76/76) plus the full suite (683/683) before commit.
- **`ENTRY_TYPE_KEYS` guard.** Gap 3 adds fields to `split_info`; if that
  introduces or renames a shared data-field name, `main.py`'s `ENTRY_TYPE_KEYS`
  registry and `tests/test_entry_schema_guard.py` must be updated in the same
  change.
- **Gap 3 only:** it touches the split/restore contract, which the auto-rollback
  change-gate and the deterministic-rehash design both depend on. Read
  [`docs/feature-split-hash-deterministic/DECISIONS.md`](../feature-split-hash-deterministic/DECISIONS.md)
  and [`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md`](../feature-auto-rollback/ROLLBACK_MECHANISM.md)
  §10 before starting.

Gaps 1 and 2 were independent of each other and of Gap 3, and shipped separately
in that order (`1af16a3`, then `e2b799c`). Gap 1 was the highest value per line of
code in the set. Gap 3 remains open and is gated on the user's opt-in decision
above; IMP-R10 remains open and is gated on the rollback change-gate.
