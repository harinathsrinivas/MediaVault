# mkvmerge Split-Capability Matrix

> **What mkvmerge will and will not `--split`, measured — not assumed.**
> Established 2026-08-24 while root-causing [`ISSUE-flac-split-failure.md`](ISSUE-flac-split-failure.md).
> Binary under test: `C:\Program Files\MKVToolNix\mkvmerge.exe` → `mkvmerge v97.0 ('You Don't Have A Clue') 64-bit`.
>
> Every row below was produced by an actual run in this environment. Re-verify with §3 after any
> MKVToolNix upgrade — this is a per-version property of mkvmerge's packetizers, not a spec guarantee.
> Companions: [`RUNBOOK-remux-before-split.md`](RUNBOOK-remux-before-split.md) · [`CODE-GAPS.md`](CODE-GAPS.md) · [`README.md`](README.md)

---

## 1. Audio codecs

Method: one synthetic MKV per codec (`libx264` video + a sine tone in the codec under test), then
`mkvmerge -o out.%03d.mkv --split size:30K in.mkv`. "Files" is how many chunks landed on disk.

| Codec | Matroska codec ID | Splits? | rc | Files | Lossless? | Notes |
|---|---|---|---|---|---|---|
| **FLAC** | `A_FLAC` | ❌ **NO** | 2 | 0 | lossless | `Error: The track ID 1 … cannot be split. Splitting tracks of this type is not supported.` **This is the blocker.** |
| **WavPack** | `A_WAVPACK4` | ✅ yes | 0 | 2 | **lossless** | The recommended FLAC replacement — see §4. |
| **PCM** | `A_PCM/INT/LIT` | ✅ yes | 0 | 2 | **lossless** | Universal support, but fat — see §5. Tested as `pcm_s16le`. |
| **Opus** | `A_OPUS` | ✅ yes | 0 | 2 | lossy | Tested as `libopus`. |
| **AC-3** | `A_AC3` | ✅ yes | 0 | 2 | lossy | Also present as tracks 2 & 3 of the real incident file; never flagged. |
| **DTS** | `A_DTS` | ✅ yes | — | — | lossy core | Not synthetically tested. Evidence is stronger than a synthetic: it is track 1 (DTS-HD MA 5.1) of the real 58 GiB incident file, and mkvmerge named *only* track 4 as unsplittable. |
| **TrueHD** | `A_TRUEHD` | ⚠️ **UNTESTED** | — | — | lossless | The ffmpeg test encode failed (`encode-fail truehd`), so the resulting file was invalid and mkvmerge rejected it with a *file-type* error, **not** a split error. **No conclusion may be drawn from that run.** Test it properly before trusting either answer. |

### 1a. Reading the result correctly

An unsplittable-track failure is **exit 2** with `Error: The track ID <N> … cannot be split.` on **stdout**.
Do not confuse it with:

- **exit 3** — `fmt::v11::format_error: argument not found`, the libfmt brace bug handled by the escape at
  `main.py:304-310`. Different failure, different cause. See [`ISSUE-flac-split-failure.md` §4a](ISSUE-flac-split-failure.md).
- **exit 2** with `Error: The type of file … could not be recognized` — a broken/invalid input, not a split
  limitation. This is exactly what the failed TrueHD encode produced.

## 2. Split modes — all six refuse FLAC

The natural next thought after "size splitting fails" is "then split by time instead". It does not work.
Every `--split` mode was run against the same FLAC test file:

| `--split` argument | rc | Files produced | Result |
|---|---|---|---|
| `size:30K` | 2 | 0 | `Error: The track ID 1 … cannot be split.` |
| `duration:00:00:04` | 2 | 0 | identical error |
| `timestamps:00:00:04,00:00:08` | 2 | 0 | identical error |
| `parts:00:00:00-00:00:04,00:00:04-00:00:08` | 2 | 0 | identical error |
| `chapters:all` | 2 | 0 | identical error |
| `frames:100` | 2 | 0 | identical error |

**The refusal lives in the FLAC packetizer's split-capability check, not in the size arithmetic.** It fires
before any output file is opened, which is why the failure is instantaneous and leaves no partial chunks.
There is no mkvmerge flag that overrides it. The only remedies are to **convert** the track (§4) or
**drop** it.

## 3. Reproducing this table

Run this after any MKVToolNix upgrade to refresh the matrix. It builds a throwaway MKV per codec and
reports rc + the first `Error:` line — **read stdout, not stderr**, which is the same trap that hid the
original incident (`main.py:314`).

```python
import subprocess, os, glob, tempfile

MK  = r"C:\Program Files\MKVToolNix\mkvmerge.exe"
TMP = tempfile.mkdtemp(prefix="mkvsplit-matrix-")
CODECS = ["flac", "wavpack", "pcm_s16le", "libopus", "ac3", "truehd", "dts"]

for c in CODECS:
    src = os.path.join(TMP, f"t_{c}.mkv")
    enc = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=12",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", c, src,
    ], capture_output=True, text=True)
    # A failed encode yields an invalid file; mkvmerge would then report a FILE-TYPE
    # error that says nothing about splittability. Skip rather than record a false row.
    if enc.returncode != 0 or not os.path.exists(src):
        print(f"{c:12s} SKIPPED - ffmpeg could not encode this codec here"); continue

    out = os.path.join(TMP, f"{c}-sp.%03d.mkv")
    r = subprocess.run([MK, "-o", out, "--split", "size:30K", src], capture_output=True, text=True)
    err = next((l for l in r.stdout.splitlines() if l.startswith("Error")), "")
    n = len(glob.glob(os.path.join(TMP, f"{c}-sp.*.mkv")))
    print(f"{c:12s} rc={r.returncode} files={n} {err[:100] or 'OK'}")
```

To re-check the split *modes* of §2, hold the input at the FLAC file and iterate the `--split` argument
over `["size:30K", "duration:00:00:04", "timestamps:00:00:04,00:00:08",
"parts:00:00:00-00:00:04,00:00:04-00:00:08", "chapters:all", "frames:100"]`.

## 4. FLAC → WavPack is bit-exact

WavPack is the replacement of choice because it is **lossless and splittable** — the conversion changes
the container codec without touching a single audio sample.

**Proof (real file, not synthetic).** A 90-second slice of track 4 of the incident file was decoded to PCM
before and after the conversion:

| Stage | Decoded PCM MD5 |
|---|---|
| original `A_FLAC` track | `62d852d98d3743bf8bf6251a394d4095` |
| after FLAC → WavPack | `62d852d98d3743bf8bf6251a394d4095` |

Identical. The audio is preserved sample-for-sample.

**Size cost**, same 90-second slice:

| Encoding | Bytes | Δ |
|---|---|---|
| FLAC (as shipped) | 6 800 082 | — |
| WavPack | 7 496 109 | **+10.2 %** |

Scaled to the full track (~631 MB → ~695 MB), that is **+64 MB on a 62.5 GB file** — around one tenth of
one percent, and it does not change the chunk count at a 6000 MB split.

The end-to-end recipe (remux → split → merge, with Dolby Vision and per-stream checksum verification) is
in [`RUNBOOK-remux-before-split.md`](RUNBOOK-remux-before-split.md).

## 5. Choosing a replacement codec

| Option | Fidelity | Size for this track | Player support | Verdict |
|---|---|---|---|---|
| **WavPack** | bit-exact | ~695 MB | ffmpeg / VLC / Kodi decode it fine; **less universal than FLAC on hardware players and some TV apps** | **Default choice.** Best fidelity-per-byte; the compatibility risk is the one thing to weigh. |
| **PCM** | bit-exact | ~4 GB (4.6 Mbps × 6904.8 s) | universal | Use when the restored file must play anywhere and ~3.3 GB of bloat is acceptable. |
| **AC-3 / E-AC-3 / Opus** | lossy | ~550 MB or less | universal (AC-3) | Defensible *specifically* when the FLAC track was lossy-sourced anyway (see the bitrate tell in [`ISSUE-flac-split-failure.md` §2](ISSUE-flac-split-failure.md)), but it is a real, irreversible quality decision. |
| **Drop the track** | track lost | −631 MB | n/a | Simplest. Only if the dub genuinely has no value to you — remember the archived copy becomes the *only* copy once `replace` swaps in the dummy. |

> ⚠️ **Whatever is chosen, the archived master differs from the original file.** MediaVault's split model
> already accepts that a restored split file is a remux rather than a byte copy — it blesses a canonical
> hash of the deterministic merge (`--deterministic <seed>`, see
> [`../feature-split-hash-deterministic/DECISIONS.md`](../feature-split-hash-deterministic/DECISIONS.md) D-1)
> — but a **codec change is a further, permanent departure** that the current `split_info` schema does not
> record anywhere. That gap is written up in [`CODE-GAPS.md`](CODE-GAPS.md).
