# Edge case — tracks `mkvmerge` refuses to split

> **The short version:** `mkvmerge` cannot `--split` a file containing a **FLAC** audio track. It
> fails with exit status 2, and `split_video_file` throws the explanation away, so the operator sees
> only `returned non-zero exit status 2`. The fix is to remux the offending track to **WavPack**
> (lossless, splittable, ~+10% on that track alone) before prepping.
>
> Discovered 2026-08-24 archiving `mov-kor-2003-ataleoftwosisters`
> (*A Tale of Two Sisters* 2003, 62.5 GB DV Profile 7 BD remux with an Italian FLAC dub).

## Read in this order

| Doc | What it answers |
|---|---|
| [`ISSUE-flac-split-failure.md`](ISSUE-flac-split-failure.md) | **What happened and why.** The incident record: terminal transcript, root cause, the two-stage loss of the error message, and the two red herrings (the `{{tmdb-4552}}` brace escape and disk space) explicitly closed so nobody re-investigates them. |
| [`CODEC-SPLIT-MATRIX.md`](CODEC-SPLIT-MATRIX.md) | **What mkvmerge can and cannot split.** Measured per-codec table, proof that all six `--split` modes refuse FLAC identically, a re-runnable script for the next MKVToolNix upgrade, and the bit-exactness proof for FLAC → WavPack. |
| [`RUNBOOK-remux-before-split.md`](RUNBOOK-remux-before-split.md) | **How to archive such a file.** Detect → choose a codec → remux → verify → the disk-space sequencing that makes deleting the original mandatory → run `prep_push_rep`. |
| [`CODE-GAPS.md`](CODE-GAPS.md) | **What MediaVault should do about it.** Three unimplemented tiers: surface mkvmerge's real error, preflight for unsplittable tracks, auto-remux. Not yet registered as IMP tasks. |

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
| This file (`mov-kor-2003-ataleoftwosisters`) | Fixed operationally — remuxed FLAC → WavPack, verified, archived. |
| MediaVault code | **Unfixed.** The next FLAC-bearing source fails the same way, after a full prep. See [`CODE-GAPS.md`](CODE-GAPS.md). |
