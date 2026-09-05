# Standalone GUI tools — Master Stream Archiver & Match Archiver

> **These are NOT part of MediaVault.** They are self-contained Tkinter desktop apps that happen to
> live in this repo's root. They share no code with `main.py` / `mainfetch.py` / `mvcommon.py`,
> import nothing from MediaVault, and touch neither `library_*.json` nor `C:\Media`.
>
> They are kept here because they operate on the same media files and share the same external
> tooling (MKVToolNix, ffmpeg). Run them directly with `python <file>.py` — never through
> `main.py`.

**Last updated:** 2026-09-06

---

## 1. Master Stream Archiver

A GUI for **inspecting media files, judging their quality, and remuxing them** — with an optional
**local LLM multi-agent review** layered on top of the algorithmic analysis.

### What it does

1. **Select files** through a file picker.
2. **Probe stats** — resolution, fps, bitrate, audio codec/channels/bitrate, HDR flag, duration, size.
3. **Real-time analysis pass** — streams ffmpeg output and parses it for defects:
   `corrupt_packets`, `discontinuities`, crashes.
4. **Algorithmic verdict** — `generate_verdict()` turns those metrics into a pass/fail judgement.
5. **Local AI review (optional)** — sends the report to **Ollama** for a second opinion.
6. **Remux** — drives `mkvmerge` to write the output file.

### The multi-agent pipeline (the `MultiModel` versions)

This is the interesting part, and it mirrors MediaVault's own worker/judge pattern — three workers
with deliberately different temperaments, then a judge that arbitrates:

| Role | Default model |
|---|---|
| Worker 1 (Gen) | `qwen3.6:27b-q4_K_M` |
| Worker 2 (Strict) | `gemma4:26b-a4b-it-q4_K_M` |
| Worker 3 (Cons) | `gpt-oss:20b` |
| Lead Judge | `huihui_ai/qwen3-coder-abliterated:30b-a3b-instruct-q4_K_M` |

All four are **editable in the UI**, as is the Ollama endpoint (default `http://localhost:11434`).
The whole agentic layer is behind an **"Enable Agentic Multi-Model Analysis"** checkbox — untick it
and you get the pure algorithmic verdict with no LLM involvement.

`build_worker_prompt()` composes the per-worker question from the algorithmic report;
`build_judge_prompt()` feeds the workers' answers to the judge. Individual agents can be
**retried independently** (`retry_single_agent`) without re-running the whole pipeline.

### Version lineage

| File | Lines | Notes |
|---|---|---|
| `Master_Stream_Archiver.py` | 419 | Base. **Single-model** `ask_ollama()` — no worker/judge split |
| `Master_Stream_ArchiverV1.py` | 419 | **Byte-identical to the base** — a backup copy |
| `Master_Stream_Archiver_MultiModel.py` | 644 | First multi-agent version |
| `..._MultiModel_v3.py` | 648 | |
| `..._MultiModel_v5.py` | 733 | |
| `..._MultiModel_v6.py` | 744 | |
| `..._MultiModel_v7.py` | 747 | |
| **`..._MultiModel_v8.py`** | **750** | **Latest — use this one** |

The numbered files are iterative drafts kept for reference, not a maintained series. If you only
want one, it's **v8**.

---

## 2. Match Archiver

A GUI for **chaptering football match recordings** to a consistent broadcast structure.

### What it does

1. **Scan a directory** (`scan_directory`) for match files.
2. **Build a chapter table** — one row per chapter, each with a dropdown pre-filled from a fixed
   list of broadcast labels.
3. **Generate MKV chapter files** (`generate_files`) with correctly formatted timestamps
   (`parse_time` → `format_mkv_time`).

### The label set

`STANDARD_CHAPTERS` — the "Golden Standard Broadcast Labels":

```
Pre-Match Presentation · First Half · First Half (Hydration Break)
First Half (Play Resumed) · Half-Time Highlights · Half-Time Analysis
Second Half Warm-Up · Second Half · Second Half (Hydration Break) · …
```

The point is consistency: every match gets the same chapter vocabulary, so navigation behaves
identically across the archive.

| File | Lines | Notes |
|---|---|---|
| **`MatchArchiver.py`** | **182** | **Current** |
| `MatchArchiver_bkp1.py` | 179 | Earlier backup — 97 differing lines |

---

## 3. Requirements

- **Python with Tkinter** (bundled with standard Windows Python).
- **MKVToolNix** at `C:\Program Files\MKVToolNix\mkvmerge.exe` — hard-coded as `MKVMERGE_PATH`;
  edit the constant if yours lives elsewhere.
- **ffmpeg / ffprobe** on `PATH` (Master Stream Archiver's analysis pass).
- **Ollama** running locally with the configured models pulled — *only* if you tick the agentic
  checkbox. Everything else works without it.

```
python Master_Stream_Archiver_MultiModel_v8.py
python MatchArchiver.py
```

---

## 4. Relationship to MediaVault

**None, technically.** No shared imports, no shared state.

The one real overlap is **MKVToolNix**. MediaVault uses `mkvmerge` for splitting and merging
(`main.py`'s `MKVMERGE_PATH`), and these tools use it for remuxing and chapters. That means the
same known constraint applies: **`mkvmerge` cannot `--split` some codecs** (FLAC is the confirmed
case — see `docs/edge-case-unsplittable-tracks/`). If you remux a file with one of these tools and
then archive it through MediaVault, that limit still bites.

MediaVault's own pre-flight (`refuse_if_unsplittable`) will catch it before any expensive work —
and `tools/remux_unsplittable.py` is the sanctioned manual fix, deliberately never invoked
automatically.

---

## 5. Repo note

These files sat **staged but uncommitted** in the index for months, which was a live hazard: a
git-agent once swept them into an unrelated feature commit (recovered only because nothing had been
pushed). Every commit in this repo therefore had to use an explicit pathspec
(`git commit -m "…" -- <paths>`), never `git add -A`.

Committing them here **removes that hazard permanently**. If you'd rather they lived elsewhere,
moving them to their own repo is clean — nothing in MediaVault references them.
