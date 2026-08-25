# Edge case — tracks `mkvmerge` refuses to split

> **The short version:** `mkvmerge` cannot `--split` a file containing a **FLAC** audio track. It
> fails with exit status 2, and `split_video_file` used to throw the explanation away, so the operator
> saw only `returned non-zero exit status 2` — after a full prep. The fix for such a file is to remux
> the offending track to **WavPack** (lossless, splittable, ~+10% on that track alone) before prepping.
>
> Both code gaps are now closed: **IMP-C19** prints mkvmerge's real error, and **IMP-C20** refuses at
> `push` before splitting and names the track. MediaVault still never converts the track for you —
> that stays your decision.
>
> Discovered 2026-08-24 archiving `mov-kor-2003-ataleoftwosisters`
> (*A Tale of Two Sisters* 2003, 62.5 GB DV Profile 7 BD remux with an Italian FLAC dub).

## Read in this order

| Doc | What it answers |
|---|---|
| [`ISSUE-flac-split-failure.md`](ISSUE-flac-split-failure.md) | **What happened and why.** The incident record: terminal transcript, root cause, the two-stage loss of the error message, and the two red herrings (the `{{tmdb-4552}}` brace escape and disk space) explicitly closed so nobody re-investigates them. |
| [`CODEC-SPLIT-MATRIX.md`](CODEC-SPLIT-MATRIX.md) | **What mkvmerge can and cannot split.** Measured per-codec table, proof that all six `--split` modes refuse FLAC identically, a re-runnable script for the next MKVToolNix upgrade, and the bit-exactness proof for FLAC → WavPack. |
| [`RUNBOOK-remux-before-split.md`](RUNBOOK-remux-before-split.md) | **How to archive such a file.** Detect → choose a codec → remux → verify → the disk-space sequencing that makes deleting the original mandatory → run `prep_push_rep`. |
| [`CODE-GAPS.md`](CODE-GAPS.md) | **What MediaVault did about it.** Surface mkvmerge's real error (**IMP-C19**, done) · preflight for unsplittable tracks (**IMP-C20**, done) · assisted remux (open, and **opt-in only** by user decision). |

## Why the obvious workarounds are dead

Both are ruled out by the Google Photos cold-storage design (`ARCHITECTURE.md` §1), not by preference:

- **Push the file unsplit** — chunks exist to stay under Photos' ~10 GB per-video cap. A 62.5 GB file
  would push to the phone fine and then never upload.
- **Split at the byte level** instead of with `mkvmerge` — Photos only ingests real playable videos, so
  byte fragments would never be indexed. **Chunks must remain valid MKVs.**

That leaves exactly one lever: the unsplittable track itself has to change.

## Status

| | |
|---|---|
| This file (`mov-kor-2003-ataleoftwosisters`) | ✅ Fixed operationally — remuxed FLAC → WavPack, verified, archived. |
| Diagnosis (**IMP-C19**, `1af16a3`) | ✅ Shipped. mkvmerge's own `Error:` line is printed by `split_video_file` *and* `merge_video_files`. `tests/test_mkvmerge_error_surfacing.py`. |
| Pre-flight (**IMP-C20**, `e2b799c` + `1b1a899`) | ✅ Shipped. Both auto-pilots refuse **before their prep leg**, and `cmd_push` again before the split. `tests/test_unsplittable_preflight.py`. |
| The fix itself (**IMP-C21**) | ✅ Shipped as `tools/remux_unsplittable.py` — a **separate manual tool** MediaVault never invokes (guard-tested), dry-run by default, never destructive. [`CODE-GAPS.md`](CODE-GAPS.md) Gap 3. |
| **IMP-R10** — PONR journal-lock race | ⏸️ Open and **change-gated**. A separate bug from the same archival run: [`../edge-case-replace-ponr-journal-lock/README.md`](../edge-case-replace-ponr-journal-lock/README.md). |

The next FLAC-bearing source is caught **before `prep`** runs, with the track named — no scan or hash
is spent. Fixing it is then one inspectable command you run yourself:
`python tools/remux_unsplittable.py "<file>"` to look, `--run` to do it.
