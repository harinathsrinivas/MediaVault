"""remux_unsplittable.py — make a file splittable, one file at a time, by hand.

mkvmerge cannot `--split` a file containing certain audio codecs (today: FLAC).
`main.py` DETECTS this and refuses the archive, naming the track — it never fixes
it, because changing a codec is an irreversible decision about what becomes the
only surviving copy once `replace` swaps in the dummy.

This script is that fix, kept deliberately separate:

  * Nothing in MediaVault ever invokes it. It is not imported by `main.py`, not
    wired into any command, and not suggested as an automatic remedy. You run it.
  * It is a DRY RUN unless you pass `--run`. By default it prints exactly what it
    would do and stops, so you can inspect each case before committing to it.
  * It NEVER deletes or overwrites the original, and refuses to clobber an
    existing output. The swap is yours to make, after you have verified.
  * It offers lossless conversions and an explicit drop. It will not silently
    re-encode to a lossy codec — if that is what you want for a given file, run
    ffmpeg yourself.

Usage
-----
    python tools/remux_unsplittable.py "<file.mkv>"                 # inspect only
    python tools/remux_unsplittable.py "<file.mkv>" --run           # do it (wavpack)
    python tools/remux_unsplittable.py "<file.mkv>" --codec pcm --run
    python tools/remux_unsplittable.py "<file.mkv>" --codec drop --run
    python tools/remux_unsplittable.py "<file.mkv>" --run --verify-streams

Background, measurements and the manual procedure:
    docs/edge-case-unsplittable-tracks/
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

# Allow running both as `python tools/remux_unsplittable.py` and as
# `import tools.remux_unsplittable` (matches tools/warm_profiles.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mvcommon import MKVMERGE_PATH
import main as mv

# Lossless replacements verified to split under mkvmerge v97.0 (see
# docs/edge-case-unsplittable-tracks/CODEC-SPLIT-MATRIX.md). `drop` removes the
# track entirely. Lossy targets are intentionally absent — this tool will not
# quietly degrade audio.
CODECS = {
    "wavpack": ("wavpack", "lossless, ~+10% on the affected track — the default"),
    "pcm": ("pcm_s16le", "lossless, universally supported, but several GB larger"),
    "drop": (None, "remove the track entirely — its audio is gone from the archive"),
}


def probe_streams(path):
    """ffprobe's view of the file — this is the indexing ffmpeg itself will use."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=index,codec_type,codec_name:stream_tags=language,title",
         "-of", "json", path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return json.loads(r.stdout).get("streams", [])


def audio_index_for(streams, overall_index):
    """Map an overall stream index to its position among AUDIO streams — the `N`
    in ffmpeg's `-c:a:N`. Getting this wrong transcodes the wrong track (on the
    incident file, `-c:a:4` would have hit a subtitle and `-c:a:1` the DTS-HD MA
    main track), so it is computed, never assumed."""
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    for pos, s in enumerate(audio):
        if s.get("index") == overall_index:
            return pos
    return None


def build_command(ffmpeg, src, dst, mapping, codec_key):
    """The exact argv. `mapping` is (overall_index, audio_index).

    `-c copy` followed by a narrower `-c:a:N` is the idiom verified against the
    real 62 GB incident file; ffmpeg applies the more specific option and warns
    "Multiple -c ... only the last option will be used", which is expected and
    correct. -stats keeps progress visible on a multi-hour remux."""
    overall_index, audio_index = mapping
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "warning", "-stats",
           "-i", src, "-map", "0"]
    if codec_key == "drop":
        cmd += ["-map", f"-0:{overall_index}", "-c", "copy"]
    else:
        cmd += ["-c", "copy", f"-c:a:{audio_index}", CODECS[codec_key][0]]
    return cmd + [dst]


def dv_record(path):
    """The Dolby Vision configuration record, or None. Compared before/after so a
    DV Profile 7 remux that silently lost its RPU cannot pass verification."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream_side_data", "-of", "json", path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    for s in json.loads(r.stdout).get("streams", []):
        for sd in s.get("side_data_list", []):
            if "DOVI" in str(sd.get("side_data_type", "")):
                return sd
    return None


def duration_of(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return round(float(r.stdout.strip()), 3)
    except (ValueError, AttributeError):
        return None


def stream_md5(path, spec, decode=False):
    """MD5 of one stream. `decode=True` compares decoded PCM, which is how a
    lossless conversion between two different codecs is proved equivalent."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-map", spec]
    if not decode:
        cmd += ["-c", "copy"]
    r = subprocess.run(cmd + ["-f", "md5", "-"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def verify(src, dst, mapping, codec_key, check_streams):
    """Returns a list of problems; empty means the remux looks faithful."""
    problems = []
    overall_index, audio_index = mapping

    src_streams, dst_streams = probe_streams(src) or [], probe_streams(dst) or []
    expected = len(src_streams) - (1 if codec_key == "drop" else 0)
    if len(dst_streams) != expected:
        problems.append(f"stream count {len(dst_streams)}, expected {expected}")

    d_src, d_dst = duration_of(src), duration_of(dst)
    if d_src is not None and d_dst is not None and abs(d_src - d_dst) > 1.0:
        problems.append(f"duration drifted: {d_src}s -> {d_dst}s")

    if dv_record(src) and dv_record(src) != dv_record(dst):
        problems.append("Dolby Vision configuration record changed or was lost")

    if check_streams:
        print("   > verifying streams (reads both files in full)...")
        for s in src_streams:
            i = s.get("index")
            if i == overall_index:
                if codec_key == "drop":
                    continue
                a, b = stream_md5(src, f"0:{i}", True), stream_md5(dst, f"0:{i}", True)
                label = f"stream {i} (decoded PCM)"
            else:
                tgt = i - 1 if (codec_key == "drop" and i > overall_index) else i
                a, b = stream_md5(src, f"0:{i}"), stream_md5(dst, f"0:{tgt}")
                label = f"stream {i}"
            if s.get("codec_type") == "subtitle" and not a and not b:
                continue  # the md5 muxer does not carry PGS; nothing to compare
            if a != b or a is None:
                problems.append(f"{label} checksum mismatch ({a} vs {b})")
            else:
                print(f"     {label}: MATCH")
    return problems


def main_cli(argv=None):
    ap = argparse.ArgumentParser(
        description="Remux a file so mkvmerge can split it. Manual, dry-run by default.")
    ap.add_argument("file", help="the .mkv to inspect")
    ap.add_argument("--codec", choices=sorted(CODECS), default="wavpack",
                    help="what to do with the unsplittable track (default: wavpack)")
    ap.add_argument("--out", help="output path (default: <name>.remux.mkv beside the source)")
    ap.add_argument("--run", action="store_true",
                    help="actually perform the remux; without this it is a dry run")
    ap.add_argument("--verify-streams", action="store_true",
                    help="after --run, checksum every stream (reads both files in full)")
    args = ap.parse_args(argv)

    src = os.path.abspath(args.file)
    if not os.path.exists(src):
        print(f"❌ No such file: {src}")
        return 2

    if not os.path.exists(MKVMERGE_PATH) and not shutil.which("mkvmerge"):
        print(f"❌ mkvmerge not found at {MKVMERGE_PATH} and not on PATH.")
        return 2
    ffmpeg = mv.resolve_ffmpeg()
    if not ffmpeg:
        print("❌ ffmpeg not found. Install it or set FFMPEG_PATH in main.py.")
        return 2

    # Same probe main.py's pre-flight uses, so this tool and the refusal that
    # sent you here can never disagree about which tracks are the problem.
    bad = mv.find_unsplittable_tracks(src)
    if not bad:
        print(f"✅ Nothing to do — mkvmerge can already split {os.path.basename(src)}.")
        print(f"   (Unsplittable codecs are {sorted(mv.UNSPLITTABLE_CODEC_IDS)}.)")
        return 0

    if len(bad) > 1:
        print(f"❌ {len(bad)} unsplittable tracks found: "
              + ", ".join(f"track {t} ({c}, {l})" for t, c, l in bad))
        print("   This tool handles one track at a time on purpose — several tracks")
        print("   means several separate decisions. Handle them with ffmpeg directly.")
        return 3

    track_id, codec_id, lang = bad[0]
    streams = probe_streams(src)
    if streams is None:
        print("❌ ffprobe could not read the file.")
        return 2

    match = next((s for s in streams if s.get("index") == track_id), None)
    if match is None or match.get("codec_type") != "audio":
        print(f"❌ mkvmerge flagged track {track_id} but ffprobe does not see an audio")
        print("   stream at that index. Refusing to guess which stream to convert.")
        return 3
    audio_index = audio_index_for(streams, track_id)
    if audio_index is None:
        print(f"❌ Could not map track {track_id} to an ffmpeg audio index.")
        return 3

    dst = os.path.abspath(args.out) if args.out else \
        os.path.splitext(src)[0] + ".remux" + os.path.splitext(src)[1]
    if os.path.exists(dst):
        print(f"❌ Output already exists, refusing to overwrite: {dst}")
        return 2

    action = (f"DROP it" if args.codec == "drop"
              else f"convert it to {CODECS[args.codec][0]}")
    print(f"File   : {src}")
    print(f"Problem: track {track_id} ({codec_id}, {lang}) — mkvmerge cannot split this codec")
    print(f"Plan   : {action}  [{CODECS[args.codec][1]}]")
    print(f"         ffmpeg audio index -c:a:{audio_index}  (overall stream {track_id})")
    print(f"Output : {dst}")

    cmd = build_command(ffmpeg, src, dst, (track_id, audio_index), args.codec)
    print("\nCommand:\n  " + " ".join(f'"{a}"' if " " in a else a for a in cmd))

    need = os.path.getsize(src)
    free = shutil.disk_usage(os.path.dirname(dst)).free
    print(f"\nDisk   : need ~{need / 1e9:.1f} GB, {free / 1e9:.1f} GB free on the target volume")
    if free < need:
        print("❌ Not enough free space for the output. Free space or pass --out elsewhere.")
        return 2
    if free - need < 2e9:
        print("⚠️  This leaves under 2 GB. Note the SPLIT afterwards needs another ~1x,")
        print("    so you will likely have to remove the original first — see the runbook.")

    if not args.run:
        print("\n— DRY RUN — nothing was changed. Re-run with --run to perform it.")
        return 0

    print("\n> remuxing...")
    if args.codec != "drop":
        print('  (ffmpeg will warn "Multiple -c ... only the last option will be used"'
              " — that is expected: it is how the single track gets a different codec.)")
    r = subprocess.run(cmd)
    if r.returncode != 0 or not os.path.exists(dst):
        print(f"❌ ffmpeg failed (exit {r.returncode}). Nothing was changed.")
        if os.path.exists(dst):
            os.remove(dst)
        return 1

    print("> verifying...")
    problems = verify(src, dst, (track_id, audio_index), args.codec, args.verify_streams)
    if problems:
        print("❌ Verification found problems — the output is KEPT so you can inspect it,")
        print("   but do not swap it in until you understand these:")
        for p in problems:
            print(f"     • {p}")
        return 1

    print("✅ Remux verified"
          + (" (stream checksums included)" if args.verify_streams else
             " (headers, duration, DV record; add --verify-streams for checksums)"))
    print(f"   {dst}")
    print("\nThe original is untouched. Nothing has been swapped — that is your call.")
    print("When you are satisfied, and having read the disk-space sequencing in")
    print("docs/edge-case-unsplittable-tracks/RUNBOOK-remux-before-split.md:")
    print(f"     delete   {src}")
    print(f"     rename   {os.path.basename(dst)} -> {os.path.basename(src)}")
    print("     then run your normal prep_push_rep")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
