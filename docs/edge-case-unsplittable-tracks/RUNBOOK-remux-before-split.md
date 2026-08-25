# Runbook — Remux Before Split (unsplittable audio tracks)

> **Operational procedure** for archiving a file that carries a track `mkvmerge`
> refuses to split. Written 2026-08-24 from the `mov-kor-2003-ataleoftwosisters`
> incident. The failure analysis is [`ISSUE-flac-split-failure.md`](ISSUE-flac-split-failure.md);
> the tested codec table is [`CODEC-SPLIT-MATRIX.md`](CODEC-SPLIT-MATRIX.md);
> the un-implemented code fixes are [`CODE-GAPS.md`](CODE-GAPS.md).
>
> Verified against **mkvmerge v97.0** and **ffmpeg 8.1.2** on 2026-08-24.

---

## 1. Detect — before `prep`, not after

`mkvmerge` cannot split a file containing certain audio codecs. It fails the
whole `--split` run with exit status **2** and writes the reason to **stdout**.
There is no partial output; nothing is written.

Since **IMP-C20** (`e2b799c`) `cmd_push` performs this detection for you: it
probes the track list with `mkvmerge -J` before splitting and refuses up front,
naming the offending track and pointing back at this runbook. And since
**IMP-C19** (`1af16a3`) mkvmerge's own `Error:` line is printed rather than
discarded, so even a split that does start explains itself if it fails.

Since the IMP-C20 follow-up (`1b1a899`) both auto-pilots — `prep_push_rep` and
`prep_push_rep_season` — run the same probe **before their prep leg**, so the
refusal now lands before the deep scan and whole-file hash, not after. The
season variant probes every episode in the folder, because `cmd_prep_season`
hashes the whole season before the first push. `cmd_push` keeps its own check
as the backstop for a plain `push` on an already-prepped entry.

You therefore no longer *have* to detect this yourself. It is still useful when
you are deciding what to do with a new file before touching MediaVault at all:

```
mkvmerge -J "<file>"
```

Look at every `codec_id` in the `tracks` array. If any of them is on the ❌ list
in [`CODEC-SPLIT-MATRIX.md`](CODEC-SPLIT-MATRIX.md) — today that means
**`A_FLAC`** — the split will fail.

**Why this ordering matters (the original failure):** `prep` runs a deep
tech-spec scan and a whole-file SHA-256 before `push` ever calls the splitter.
On the 62 GB incident file that was a long scan thrown away, and the
auto-rollback then correctly reverted the library entry and the `uid` /
`<short_id>.sha256` sidecars, so the whole thing had to be redone from scratch.
The first IMP-C20 commit only gated at `push`, which did not fix that — the
follow-up moved the gate ahead of `prep`, which is what actually closes it.

A one-liner for the common case:

```
mkvmerge -J "<file>" | python -c "import json,sys; [print(t['id'], t['type'], t['properties'].get('codec_id'), t['properties'].get('language')) for t in json.load(sys.stdin)['tracks']]"
```

---

### The tool (IMP-C21) — `tools/remux_unsplittable.py`

Since 2026-08-25 the whole procedure below is packaged as a standalone script.
**MediaVault never runs it** — that is the point; you run it, one file at a time:

```
python tools/remux_unsplittable.py "<file.mkv>"                  # inspect: names the track,
                                                           # prints the exact ffmpeg argv
                                                           # and the disk arithmetic
python tools/remux_unsplittable.py "<file.mkv>" --run            # do it (wavpack, lossless)
python tools/remux_unsplittable.py "<file.mkv>" --run --verify-streams   # + per-stream checksums
python tools/remux_unsplittable.py "<file.mkv>" --codec drop --run       # drop the track instead
```

It is a **dry run unless you pass `--run`**, never overwrites or deletes anything,
computes the `-c:a:N` index for you (§3's trap), and verifies the result before
telling you it succeeded. The swap — delete original, rename remux into place —
it deliberately leaves to you, because of §6.

The manual procedure below is still the reference, and still what you want when a
file needs something the tool refuses to guess at: more than one unsplittable
track, a lossy target, or any case you'd rather drive by hand.

## 2. Decide — what the offending track becomes

| Option | When | Cost |
|---|---|---|
| **WavPack** (default) | Almost always. Lossless, splittable, bit-exact round-trip. | ~**+10%** on that one track only |
| Drop the track (`-a !<id>`) | The dub genuinely isn't wanted | Loses that audio permanently |
| PCM | Maximum player compatibility beats size | Large — for the incident track, ~4 GB vs ~631 MB |

**WavPack is the default and was the choice for the incident file.** It is
lossless, `mkvmerge` splits it without complaint, and the decoded audio is
bit-identical to the FLAC source (proved in §4).

Sizing check before you commit to it — the offending track is usually a small
fraction of the file. For the incident file the Italian FLAC ran at
**730,976 bps** over **6904.792 s** ≈ **631 MB** of a 62.56 GB file (1%), so the
WavPack conversion added roughly **+64 MB** to a 62.6 GB archive. Chunk count
was unaffected.

Read the per-track bitrate straight off the container's statistics tags:

```
ffprobe -v error -show_entries format=duration -show_entries stream=index,codec_name,channels:stream_tags=BPS,language,title -of default=noprint_wrappers=0 "<file>"
```

---

## 3. The recipe

```
ffmpeg -i "<src>" -map 0 -c copy -c:a:3 wavpack "<out>"
```

`-map 0` takes every track; `-c copy` remuxes all of them untouched; only the
stream named by `-c:a:3` is re-encoded.

### ⚠️ The index trap — recompute `-c:a:3` for every file

`-c:a:3` means **the 4th _audio_ stream**, not overall stream 3. In the incident
file the audio streams are overall 1, 2, 3, 4, so the FLAC at **overall stream 4**
is **audio index 3**:

| overall stream | codec | audio index | ffmpeg specifier |
|---|---|---|---|
| 0 | `V_MPEGH/ISO/HEVC` | — | `v:0` |
| 1 | `A_DTS` (DTS-HD MA 5.1, kor) | 0 | `a:0` |
| 2 | `A_AC3` (commentary, kor) | 1 | `a:1` |
| 3 | `A_AC3` (commentary, kor) | 2 | `a:2` |
| **4** | **`A_FLAC` (5.1 16-bit 48 kHz, ita)** | **3** | **`a:3`** |
| 5–7 | `S_HDMV/PGS` (eng, fre, fre) | — | — |
| 8 | `S_TEXT/UTF8` (und) | — | — |

**Never copy the `3` from this document into another file's command.** Recompute
it from `ffprobe` / `mkvmerge -J` every time. Getting it wrong silently
re-encodes the wrong track — most destructively, it would transcode the DTS-HD MA
main track.

Chapters and container title carry over automatically with `-map 0`; the
incident file had neither (`chapters: []`, `title: None`).

---

## 4. What the verification proved

The recipe is trusted because the full round-trip was executed on a 2-minute
excerpt of the real file on 2026-08-24, not reasoned about.

**Bit-exactness of the codec change** — decoded PCM of a 90 s slice, FLAC source
vs WavPack conversion:

```
FLAC    -> MD5=62d852d98d3743bf8bf6251a394d4095
WavPack -> MD5=62d852d98d3743bf8bf6251a394d4095    IDENTICAL
```

Same slice on disk: FLAC 6,800,082 B vs WavPack 7,496,109 B (**+10.2%**).

**Full round-trip** — remux → `mkvmerge --split size:150M` → **7 chunks** →
`mkvmerge --deterministic 7cdcd3` merge back. Per-stream MD5 against the
pre-split file:

| stream | codec | result |
|---|---|---|
| 0 | HEVC video | `33b8b50575841be4db1dd49adb3ed899` — **MATCH** |
| 1 | DTS | `60b75f405df5967491d110bad75db26c` — **MATCH** |
| 4 | WavPack | `6caa53453a3c50d93a87794e1a580c82` — **MATCH** |

**Dolby Vision survived both the remux and the split.** The DOVI configuration
record is intact in the remux and in the chunks: `dv_profile=7`, `dv_level=6`,
`rpu_present_flag=1`, `el_present_flag=1`, `bl_present_flag=1`,
`dv_bl_signal_compatibility_id=6`, `dv_md_compression=none`. This matters — the
incident file is a dual-layer DV Profile 7 BD remux, the exact case where a
careless remux loses the RPU.

**All 9 tracks and their languages were present in every chunk**, with track 4
correctly showing as `A_WAVPACK4` / `ita`.

---

## 5. Verify your own remux before deleting anything

Run all three. They are header-only or cheap; none of them re-reads 62 GB.

```
# a. track list — same count, same languages, offending track now A_WAVPACK4
mkvmerge -J "<out>"

# b. Dolby Vision / HDR side data must still be there
ffprobe -v error -select_streams v:0 -show_entries stream_side_data -of json "<out>"

# c. duration and size sanity — duration identical, size within ~+0.2%
ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 "<out>"
```

Only after (a), (b) and (c) look right do you delete the original.

---

## 6. ⚠️ Disk-space sequencing — the part that bites

**You cannot hold the original, the remux, and the chunks at the same time.**
Each is ~1X the file size, and `cmd_push`'s `_free_space_ok` preflight
(`main.py:4715`) will hard-stop the split if the volume cannot take another 1X
plus a `max(1%, 2 GB)` buffer.

Numbers from the incident machine on 2026-08-24 — source **62,556,971,814 B**,
and **C: was the only volume with room** (C: 70.5 GB free of 710.9; D: `DATA`
21.9 GB free; E: `OS` 6.8 GB free), so **`temp_dir` could not help** — there was
no second volume to redirect `_parts/` onto.

| # | Step | C: free after |
|---|---|---|
| 0 | start | 70.5 GB |
| 1 | remux to `remux.tmp.mkv` (+62.6 GB) | ~7.9 GB |
| 2 | verify (§5) — costs nothing | ~7.9 GB |
| 3 | **delete the original** | ~70.4 GB |
| 4 | rename `remux.tmp.mkv` to the original filename | ~70.4 GB |
| 5 | `prep_push_rep … SIZE_MB 6000` — split needs ~62.6 GB + ~2 GB buffer ≈ **64.6 GB** | ~5.8 GB margin |

Step 3 is **not optional**. Attempting the split while both copies exist leaves
~7.9 GB against a ~64.6 GB requirement and fails the preflight before anything
is created.

### Do NOT pass the `rehash` token here

Eager re-hash merges the chunks back into a temp file to bless the canonical
hash immediately, so it needs **2X** — roughly **125 GB** for this file, which
does not fit. Omit the token: deferred is the default, `re_hashed` stays `False`,
and the canonical hash is blessed at the first restore instead. `cmd_push`
prints this hint itself when the 2X preflight fails.

---

## 7. Why the obvious shortcuts don't work

Both of these look attractive and both are dead ends, for the same architectural
reason. Per [`ARCHITECTURE.md`](../../ARCHITECTURE.md), MediaVault's cold-storage
tier is **a Pixel phone's Google Photos auto-upload** — chunks are pushed to the
phone and the Photos app uploads them at original quality.

- **Push it unsplit.** Dead. Chunks exist to stay under Google Photos' ~10 GB
  per-video cap. A 62.5 GB single file pushes to the phone fine (the device had
  87 GB free) but Photos would never upload it, so it would never reach cold
  storage — which is the entire point.
- **Raw byte-splitting** (`split` / `cat` instead of `mkvmerge`). Dead. Google
  Photos only ingests real playable videos; byte fragments are not videos and
  would never be indexed or uploaded.

**Chunks must remain valid, playable MKVs.** That constraint is what forces the
remux: the offending track has to change, because the splitter cannot.

---

## 8. Quick reference

```
# 1. detect
mkvmerge -J "<src>"                      # any A_FLAC?

# 2. find the audio index of the offending overall stream (see §3)

# 3. remux — only that track re-encoded
ffmpeg -i "<src>" -map 0 -c copy -c:a:<n> wavpack "<dir>\remux.tmp.mkv"

# 4. verify (§5): mkvmerge -J  +  ffprobe side_data  +  duration/size

# 5. delete the original, rename remux.tmp.mkv to the original filename

# 6. archive — no `rehash` token
python main.py prep_push_rep <id> "<path>" SIZE_MB 6000
```
