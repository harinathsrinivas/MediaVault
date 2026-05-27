"""
Generate valid mkv dummy files at varying sizes for Plex detection testing.

For each target size, produces a valid H.264 + PCM-audio mkv file. The goal is
to find the minimum file size at which Plex correctly indexes a series episode.

Strategy: PCM s16le audio gives a constant 176_400 bytes/sec data rate. Video
is low-bitrate H.264 baseline (~6 KB/s for static black). Combined rate is
~182_650 bytes/sec, so duration = (target - container_overhead) / combined_rate
hits the target within a few percent.

For very small targets (10-50 KB), duration falls below 0.05s; ffmpeg will still
produce at least one video frame, so actual size may overshoot. Real sizes are
printed in the summary so the user can choose the file closest to their target.

Output: C:\\Users\\harin\\PycharmProjects\\MediaVault\\dummy_size_test\\dummy_<label>.mkv
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

OUTPUT_DIR = r"C:\Users\harin\PycharmProjects\MediaVault\dummy_size_test"
os.makedirs(OUTPUT_DIR, exist_ok=True)

PCM_RATE = 44100 * 2 * 2          # pcm_s16le stereo 44.1kHz = 176_400 B/s
VIDEO_BYTES_PER_SEC = 6_250        # libx264 baseline 50 kbps on black
COMBINED_RATE = PCM_RATE + VIDEO_BYTES_PER_SEC
CONTAINER_OVERHEAD = 5_000          # mkv header + seek index

TARGETS = [
    ("10kb",      10_000),
    ("20kb",      20_000),
    ("50kb",      50_000),
    ("100kb",    100_000),
    ("200kb",    200_000),
    ("500kb",    500_000),
    ("1mb",    1_000_000),
    ("2mb",    2_000_000),
    ("5mb",    5_000_000),
    ("10mb",  10_000_000),
    ("20mb",  20_000_000),
    ("50mb",  50_000_000),
    ("100mb",100_000_000),
]


def pick_resolution(target):
    if target < 50_000:
        return "128x72"
    if target < 500_000:
        return "320x180"
    if target < 5_000_000:
        return "640x360"
    return "1280x720"


def build_cmd(label, target):
    output_path = os.path.join(OUTPUT_DIR, f"dummy_{label}.mkv")
    resolution = pick_resolution(target)

    raw_duration = (target - CONTAINER_OVERHEAD) / COMBINED_RATE
    duration = max(0.05, round(raw_duration, 3))

    cmd = [
        FFMPEG,
        "-f", "lavfi", "-i", f"color=c=black:s={resolution}:r=24",
        "-f", "lavfi", "-i", "anullsrc=cl=stereo:r=44100",
        "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-b:v", "50k",
        "-c:a", "pcm_s16le", "-ac", "2", "-ar", "44100",
        "-t", str(duration),
        "-shortest",
        "-loglevel", "error", "-nostdin", "-y",
        output_path,
    ]
    return cmd, output_path, resolution, duration


def main():
    results = []
    for label, target in TARGETS:
        cmd, output_path, resolution, duration = build_cmd(label, target)
        print(f"Generating dummy_{label}.mkv  target~{target:,}B  res={resolution}  dur={duration}s")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()[-5:]
            print("  FAILED:")
            for line in tail:
                print(f"    {line}")
            continue
        if not os.path.exists(output_path):
            print("  FAILED: output file not created")
            continue
        actual = os.path.getsize(output_path)
        diff_pct = (actual / target - 1) * 100
        print(f"  -> {actual:,} bytes ({diff_pct:+.0f}% vs target)")
        results.append((label, target, actual))

    print()
    print(f"{'Label':<10} {'Target':>15} {'Actual':>15} {'Diff':>8}")
    print("-" * 52)
    for label, target, actual in results:
        diff_pct = (actual / target - 1) * 100
        print(f"{label:<10} {target:>15,} {actual:>15,} {diff_pct:>+7.0f}%")


if __name__ == "__main__":
    main()
