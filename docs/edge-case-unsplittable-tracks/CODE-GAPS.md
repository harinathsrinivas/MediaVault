# Code Gaps — what the unsplittable-track incident exposed

> **Status: NOT implemented. NOT registered as IMP tasks.** Nothing in this
> document has been built, and no `IMP-XN` code has been assigned to any of it.
> Registering these would mean a tier-file entry plus the
> [`improvements/PRIORITY.md`](../../improvements/PRIORITY.md) +
> [`priority-graph.html`](../priority-graph/priority-graph.html) update that
> [`CLAUDE.md`](../../CLAUDE.md) requires — that decision is still open.
>
> Written 2026-08-24 from the `mov-kor-2003-ataleoftwosisters` incident.
> Background: [`ISSUE-flac-split-failure.md`](ISSUE-flac-split-failure.md) ·
> [`CODEC-SPLIT-MATRIX.md`](CODEC-SPLIT-MATRIX.md) ·
> [`RUNBOOK-remux-before-split.md`](RUNBOOK-remux-before-split.md)

---

## Gap 1 — the split failure is undiagnosable

**Severity: high. Effort: two lines.** This is the gap that turned a one-line
explanation into a multi-hour investigation.

`mkvmerge` writes its `Error:` lines to **stdout**, not stderr. `split_video_file`
sends stdout to `DEVNULL` and captures a stderr that is then never read:

```python
# main.py:313-320
        # Added stderr capture to output real error messages
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        chunks = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".mkv")])
        print(f"   > Done. Generated {len(chunks)} parts.")
        return chunks
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running mkvmerge for splitting: {e}");
        return []
```

Three defects stacked:

1. `main.py:314` — `stdout=subprocess.DEVNULL` discards the only stream that
   carries the diagnosis.
2. `main.py:318-320` — the handler prints the `CalledProcessError` repr (argv +
   exit code) and never touches the captured `e.stderr`.
3. `main.py:313` — the comment claims the opposite of what the code does.

Net effect for the operator: `returned non-zero exit status 2` and nothing else.
The message that was thrown away was
`Error: The track ID 4 from the file '…' cannot be split. Splitting tracks of this type is not supported.`

**`merge_video_files` has the identical defect, and it is worse there.**
`main.py:342` is `subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)` —
stdout discarded and stderr not even captured — with the handler at
`main.py:345-347` printing only the repr. A merge failure happens during
**restore**, i.e. when the local master is already a dummy and the chunks are
the only copy. That is the single worst moment in the system to be blind.

**Fix:** capture both streams (`capture_output=True`) and echo them on failure,
in both functions. Keep the happy path silent.

---

## Gap 2 — no preflight for unsplittable tracks

**Severity: medium. Effort: small.**

Nothing checks whether the source *can* be split before the pipeline commits to
splitting it. The failure therefore lands in `push`, after `prep` has already
spent a deep tech-spec scan and a whole-file SHA-256 on a 62 GB file — all of
which auto-rollback then correctly discards.

**Fix:** scan with `mkvmerge -J` and hard-stop if any track's `codec_id` is on
the unsplittable list (see [`CODEC-SPLIT-MATRIX.md`](CODEC-SPLIT-MATRIX.md)),
with a message that names the track and points at the runbook:

```
❌ Track 4 (A_FLAC, ita) cannot be split by mkvmerge.
   See docs/edge-case-unsplittable-tracks/RUNBOOK-remux-before-split.md
```

**Where:** alongside the existing `_free_space_ok` preflight in `cmd_push`
(`main.py:4643`). That call site is the established pattern for this shape of
check — a read-only test that runs **before** any `makedirs` or journal record,
so it is a clean early return with nothing to roll back, exactly as the
`[SPLIT-HASH] HARD DISK PRE-FLIGHT` comment block describes. Ideally the same
check also runs at `prep` time so the scan is never wasted.

---

## Gap 3 — opt-in assisted remux (NOT automatic)

**Severity: low (quality-of-life). Effort: real feature.
Status: scoped down by user decision, 2026-08-24.**

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
> Gap 2: stop, name the track, print the command, and let the operator run it.

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
  ship, and before committing any code-touching step.
- **`ENTRY_TYPE_KEYS` guard.** Gap 3 adds fields to `split_info`; if that
  introduces or renames a shared data-field name, `main.py`'s `ENTRY_TYPE_KEYS`
  registry and `tests/test_entry_schema_guard.py` must be updated in the same
  change.
- **Gap 3 only:** it touches the split/restore contract, which the auto-rollback
  change-gate and the deterministic-rehash design both depend on. Read
  [`docs/feature-split-hash-deterministic/DECISIONS.md`](../feature-split-hash-deterministic/DECISIONS.md)
  and [`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md`](../feature-auto-rollback/ROLLBACK_MECHANISM.md)
  §10 before starting.

Gaps 1 and 2 are independent of each other and of Gap 3, and can ship separately
in either order. Gap 1 is the highest value per line of code in the set.
