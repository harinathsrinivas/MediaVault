"""
Generate valid .mp4 dummy files at varying sizes for Plex detection testing.

.mp4 rejects PCM s16le (ISO-BMFF spec), so we use AAC. AAC compresses silence
near-perfectly, so we drive it with a 440 Hz sine tone — that gives AAC enough
entropy to actually hit the requested bitrate. For larger targets we also swap
the video source from `color=black` to `testsrc` (a colorful moving pattern)
which h264 cannot crush to ~0 bytes the way it can with static black.

Output: C:\\Users\\harin\\PycharmProjects\\MediaVault\\dummy_size_test_mp4\\dummy_<label>.mp4
"""

import os
import shutil
import subprocess
import sys

FFMPEG = r"C:\Users\harin\AppData\Roaming\Emby-Server\system\ffmpeg.exe"
if not os.path.exists(FFMPEG):
    FFMPEG = shutil.which("ffmpeg")
    if not FFMPEG:
        print("ffmpeg not found")
        sys.exit(1)

OUTPUT_DIR = r"C:\Users\harin\PycharmProjects\MediaVault\dummy_size_test_mp4"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# (label, audio_kbps, video_kbps, duration_sec, video_source_lavfi)
TARGETS = [
    ("5kb",    32,    30,   0.3,  "color=c=black:s=128x72:r=10"),
    ("10kb",   64,    50,   0.5,  "color=c=black:s=128x72:r=10"),
    ("20kb",   96,   100,   1.0,  "color=c=black:s=128x72:r=15"),
    ("50kb",  128,   150,   2.0,  "color=c=black:s=160x90:r=15"),
    ("100kb", 192,   200,   3.0,  "color=c=black:s=320x180:r=15"),
    ("200kb", 256,   300,   5.0,  "color=c=black:s=320x180:r=15"),
    ("500kb", 320,   500,   5.0,  "testsrc=s=320x180:r=15"),
    ("1mb",   320,  1000,   5.0,  "testsrc=s=320x180:r=24"),
    ("5mb",   320,  2000,  15.0,  "testsrc=s=640x360:r=24"),
    ("10mb",  320,  2000,  30.0,  "testsrc=s=640x360:r=24"),
    ("50mb",  320,  4000,  90.0,  "testsrc=s=1280x720:r=24"),
    ("100mb", 320,  8000,  90.0,  "testsrc=s=1280x720:r=24"),
]


def build_cmd(label, audio_kbps, video_kbps, duration, video_source):
    output_path = os.path.join(OUTPUT_DIR, f"dummy_{label}.mp4")
    return [
        FFMPEG,
        "-f", "lavfi", "-i", video_source,
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-b:v", f"{video_kbps}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ac", "2", "-ar", "44100",
        "-t", str(duration), "-shortest",
        "-loglevel", "error", "-nostdin", "-y", output_path,
    ], output_path


def main():
    results = []
    for label, audio_kbps, video_kbps, duration, video_source in TARGETS:
        cmd, output_path = build_cmd(label, audio_kbps, video_kbps, duration, video_source)
        print(f"Generating dummy_{label}.mp4  dur={duration}s  v={video_kbps}k  a={audio_kbps}k  src={video_source[:30]}...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()[-5:]
            print("  FAILED:")
            for line in tail:
                print(f"    {line}")
            continue
        actual = os.path.getsize(output_path)
        print(f"  -> {actual:,} bytes")
        results.append((label, actual))

    print()
    print(f"{'Label':<10} {'Actual size':>15}")
    print("-" * 28)
    for label, actual in results:
        if actual >= 1_000_000:
            human = f"{actual/1_000_000:.1f} MB"
        elif actual >= 1_000:
            human = f"{actual/1_000:.1f} KB"
        else:
            human = f"{actual} B"
        print(f"{label:<10} {actual:>15,}  ({human})")


if __name__ == "__main__":
    main()
