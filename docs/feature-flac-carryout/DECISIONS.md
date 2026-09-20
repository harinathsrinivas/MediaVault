# FLAC carry-out — Decision Record

Status legend: ✅ confirmed by experiment ·  ☑️ accepted default ·  ◉ resolved in planning

## ✅ D-1. The mechanism is "carry-out as a Matroska attachment", not conversion

Measured on the real 53.35 GB Black Panther REMUX (8ch/7.1 English FLAC, track 2):

| Round-trip | Byte-exact? | Evidence |
|---|---|---|
| FLAC → tiny video + FLAC as **MKV attachment** → extract | ✅ YES | md5 identical at 1.91 GB (`38be9270…`) |
| FLAC re-muxed as an `A_FLAC` audio track → extract | ❌ NO | mkvmerge regenerates STREAMINFO; md5 drifts |

Therefore the FLAC is carried as an **opaque attachment**, never as a re-muxed audio track.
This is the same primitive the user wants for ISO later (bytes in → bytes out).

## ✅ D-2. Google Photos preserves the holder byte-identically

User uploaded `black_panther_flac_holder.mkv` to Google Photos and re-downloaded it: holder
vs download SHA256 both `17CC9931D90832DA950E22062ED05CC84D12E4170B139919F5B47568F91105`.
The premise holds.

## ✅ D-3. Deterministic merge *with the FLAC re-added* is still deterministic

Two `mkvmerge --deterministic 76faec -o out chunk1 +chunk2 +chunk3 <flac>` runs produced
identical SHA256 `ffe1b8d3…` and identical size, FLAC re-added as track 4. So the existing
h1 → split → h2-bless → compare-against-h2 loop is unchanged: h2 already includes the
re-added FLAC because the re-add is INSIDE the single `--deterministic` run.

## ✅ D-4. Engage only when FLAC is present AND a split is requested

The carry-out lives only inside `cmd_push`'s `should_split` branch (where
`refuse_if_unsplittable` already fires). A FLAC file pushed **without** split (e.g. an 8 GB
movie under the Photos cap) uploads whole — byte-for-byte today's behavior. This satisfies
"if audio is FLAC but no split — directly uploaded, no change."

## ✅ D-5. The holder lives beside the chunks, hash-routed like a chunk

Holder name `<base> [<short_id>].holder.mkv` in the same `_parts` base (so `tempdir`
relocation still applies). It is fetched by the SAME hash-routing as chunks and staged into
`restore/`. No new download mechanism, no new storage dir — a new *kind of file* in the
existing folders.

## ✅ D-6. Provenance is recorded in `split_info.carried_out_tracks`

```
"split_info": {
  … existing is_split/method/val/total_chunks/chunks …,
  "carried_out_tracks": [
    {"track_id": 2, "codec": "A_FLAC", "language": "eng", "channels": 8,
     "position": 2, "default": false, "forced": false, "enabled": true, "name": null,
     "holder_filename": "… [<short_id>].holder.mkv", "holder_hash": "<sha256>",
     "original_tracks": [
        {"type": "video", "codec_id": "V_MPEGH/ISO/HEVC", "language": "eng",
         "default": true, "forced": false, "enabled": true, "name": null, "uid": "…"},
        … one entry per original track, in order …
     ]}
  ]
}
```

Additive (no existing key changed) and rides the already-journalled
`record_set_field("split_info")`, so a pre-upload rollback reverts it cleanly — no
change-gate violation. `position` + the flag fields re-add the FLAC at its place; the
**`original_tracks` manifest** is what makes reconstruction generic — `cmd_restore` rebuilds
`--track-order` and every per-track flag from it (D-9), matched by `uid`.

## ✅ D-7. Change-gate stance — no PONR movement, no journal-format change

PONR locations, journal format/durability, `recover_journal` semantics, and
`RollbackHardFail` contract are all preserved. The holder is a new `record_create_file`
(created-this-run), the SAME pre-authorized pattern as `checksums/`/`_parts/`. The merge
re-add happens pre-PONR inside the existing reproducible-output window.

## ✅ D-8. No codec conversion, no dropping — the standing rule is honored differently

The 2026-08-25 "never auto-convert" decision is *not* reversed as a conversion: the FLAC
codec is untouched. What changed is the *container journey* — and it engages automatically
only for the one case the user explicitly approved (FLAC + split). This is recorded so the
"never auto-convert" memory is understood as "never *convert or drop*", not "never carry
out".

## ✅ D-9. Track ORDER + FLAGS + NAMES reconstructed GENERICALLY (not sample-specific)

The user's bar: the restored file's track order, `default`/`forced`/`enabled`, **language**,
and **track_name** must match the original for **every** track — the carried FLAC must NOT
be appended at the end, must not become default merely because it is re-added, and any
track's name/label/forced/default state must be reproduced exactly. This must work for ANY
file (any codec set, any language, any labels, any default/forced assignment), never for
this sample alone.

The mechanism is entirely **driven by a recorded manifest**, never by hardcoded IDs or
order strings:

1. **At carry-out**, record a complete **track manifest**: for the ORIGINAL file, the
   ordered list of every track's `{type, codec_id, language, default, forced, enabled,
   track_name, uid}`. This is the single source of truth for reconstruction. (The FLAC's
   own record in `carried_out_tracks` carries its position + the same flag fields; the full
   manifest can live alongside it, e.g. `split_info.carried_out_tracks[].original_tracks`.)
2. **At restore**, the merge builder regenerates the mkvmerge argv from that manifest:
   - `--track-order` is computed so the merged output's track list is **identical in order
     and UID** to the recorded manifest — every track, not just the FLAC. Tracks are matched
     by **UID** (stable through split) so renumbering never breaks the mapping.
   - The carried FLAC's lost MKV-level flags are re-applied via per-file options placed
     **immediately before** the `.flac` input: `--language`, `--default-track`,
     `--forced-track`, `--track-name` (and `--enabled-track` when the file has one), for
     its recorded `{language, default, forced, enabled, name}`.
   - Any OTHER track whose flags mkvermerge would perturb during re-mux (e.g. a
     `default=True` or `forced=True` track, or a `track_name` that gets remapped) is also
     re-applied from the manifest — so nothing silently flips merely because of re-mux
     ordering.

3. **FIDs are computed, not assumed.** `--track-order` FIDs are **physical command-line file
   positions** under `+`-append: with N chunks the FLAC (the last physical input) is `FID N`.
   The builder derives N from `len(chunks)`.

4. **Cover art / attachments are out of band.** In a real REMUX the cover is an
   `image/jpeg` ATTACHMENT (`cover.jpg`), not a track (note: a `ffmpeg -c copy` slice can
   *promote* it to a `V_MJPEG` track, which is how the first spike's slice differed from the
   real file — a lesson against deriving order from any slice/form of the file). Attachments
   are copied verbatim by mkvmerge and need no `--track-order`. The manifest records tracks
   only; attachments are preserved independently.

Non-goal: **byte-identical container** is explicitly NOT the target (mirrors the existing
deterministic-rehash contract — the restored file is a stable remux of the source, never the
source bytes). The target is: **same track set, same order, same per-track flags/names/
languages**, and a deterministic hash stable across restores.

Verified on the real 53.35 GB file (H2 == H3) and on a slice, with the merge builder driven
by the recorded layout (matched by UID). The concrete argv for Black Panther was:
`--track-order 0:0,0:1,6:0,0:2,0:3,0:4,0:5,0:6,0:7`, `--language 0:eng --default-track 0:0
--forced-track 0:0` — reproduced here ONLY as a worked example, not as the implementation;
the code derives these from the manifest.

## ◉ OD (open decision) — none blocking

- Holder stub video: **10 s testsrc H.264+AAC** (the clip the user already uploaded to
  Google Photos successfully), payload always carried as a Matroska attachment.
- ISO/BluRay payload generalization → deferred to a separate IMP (primitive is shared).
- Multi-track FLAC / non-FLAC unsplittable → still refuse (unchanged).

## ✅ D-10. The deterministic re-merge must use `--append-mode track` (split-sync)

**Bug found after merge:** a restored file's carried-out FLAC was ~4.57 s out of sync by
the end of a 2h41m movie. Root cause: `merge_video_files` used mkvmerge's DEFAULT
`--append-mode file`, which offsets each appended chunk by the **whole file's** highest end
timestamp (across ALL tracks). When video/audio/subtitle tracks end at different times
(video 9678.669 s, AC3 tracks 9678.688 s in the source), the default mode over-gaps each
chunk boundary and **stretches the merged video timeline** (measured: a 290 s slice →
291.35 s; the full 9678.669 s video → 9683.237 s — the same ~0.47 % error).

The carried-out FLAC is re-added at its TRUE length (9678.669 s), so it no longer lines up
with the stretched video — hence the desync. This was invisible before because a normal
file re-splits ALL tracks uniformly (they stretch together and stay mutually in sync); the
FLAC carry-out breaks that uniformity.

**Fix (verified):** emit `--append-mode track` in `merge_video_files` (global option, after
`--deterministic`, before `-o`). It offsets each appended chunk by **that track's own** end
timestamp, preserving the exact timeline: the 290 s slice merges back to 290.041 s, and a
full FLAC re-add reproduces the original video/flac == 9678.669 s. Determinism is unchanged
(`--append-mode track` is a fixed global flag; `det` and `nondet` produce the same timeline).

Source of truth: the freshly re-downloaded original at
`D:\Downloads\Torrents\Movies\Black.Panther.Wakanda.Forever.2022.4K.HDR.DV.2160p.BDRemux
Ita Eng x265-NAHOM.mkv` has video == FLAC == **9678.669 s, in perfect sync** (the restored
mis-synced file had stretched video to 9683.237 s).
