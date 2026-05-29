# Improvements — Tier G · Lessons From Similar Projects

> Cross-cutting design lessons drawn from production-grade adjacent projects. These are not standalone tasks to implement directly — they are inputs that should shape decisions in Tiers A–F. Each lesson lists which Tier A–F task it most strongly informs.

> **Cross-cutting context:**
> - MediaVault sits in a small but real ecosystem of "split-large-files-and-upload-to-free-cloud-storage" tools. rclone (chunker + gphotosdl) and tdl (Telegram) are the most mature.
> - Google Photos API policy as of **March 31, 2025**: third-party apps can only download photos that they themselves uploaded. Since the user's Pixel phone Google Photos app uploads them (not a MediaVault API client), the official API is permanently unavailable for restore. **Selenium-style browser automation is the only path forward.**
> - Jellyfin is the de facto open-source media browser. Its plugin model is the cheapest path to the future Apple TV UI ([[project_future_apple_tv_ui]]).

---

## IMP-G1: Adopt rclone chunker patterns for push reliability

- Category: refactor
- Priority: medium
- Files: `cmd_push` (535-704), entry `split_info` schema, remote upload paths on phone
- Informs: IMP-A1 (mvcommon), IMP-C8 (post-push verify), IMP-C2 (retry)
- Current behavior: MediaVault's split + push is bespoke. rclone's `chunker` backend (https://rclone.org/chunker/) solves the same problem in production with several patterns worth adopting:
  - Chunks upload to a **temporary suffix** (`.partial` / `.rclone_chunk_pending`). Only on full successful upload does an **atomic rename** swap them to the final name. A partial upload is never observable as a "complete" chunk.
  - **Composite-file JSON metadata** stored alongside chunks on the remote — records chunk count, sizes, hashes. Allows server-side verification independent of the source-of-truth library.
  - Hash modes: `md5all`, `sha1all`, `md5quick`, `sha1quick` — configurable trade-offs between full-file integrity and per-chunk speed.
  - Filename overhead awareness: rclone reserves 17 chars per chunk name to stay safe on backends with 255-char limits.
- Proposed change:
  - **Upload to `<final>.partial` then `adb shell mv`** — apply the atomic-rename pattern. Partial uploads on the phone are no longer mistakenly indexed by Google Photos as complete chunks.
  - **Write a `<base>.mvmeta.json` sidecar to /sdcard/Media** alongside chunks. Mirrors the `split_info` JSON. If `library_series.json` is ever destroyed, the remote sidecars contain enough info to rebuild the library.
  - **Audit MediaVault chunk filename overhead**: ` [<short_id>].chunk.NNN.mkv` is ~25 chars. For long titles approaching 230 chars, the path can exceed Windows' 260-char path limit.
- Rationale: rclone has solved the partial-upload-looks-complete failure mode and the rebuild-from-remote disaster-recovery story. Adopting these patterns is cheap and gets MediaVault to industry-grade reliability.
- Goal: Partial uploads are never observable as complete. Remote-side recovery is possible without the local library.
- Effort estimate: medium
- Status: done

---

## IMP-G2: Evaluate replacing mainfetch.py with rclone's gphotosdl

- Category: refactor
- Priority: high
- Files: potentially replace `mainfetch.py` (507 lines) with a thin wrapper around external `gphotosdl` binary
- Informs: IMP-C5 (fallback search), IMP-C6 (session expiry), IMP-A1 (mvcommon)
- Current behavior: `mainfetch.py` is a hand-rolled Selenium driver. It works for the user's setup. But it is the most fragile piece of MediaVault — every Google Photos UI change can break it. rclone's project ships **gphotosdl** (https://github.com/rclone/gphotosdl), a Go binary that does exactly this — runs a headless Chrome via Selenium and exposes an HTTP API for downloads. It's actively maintained because **the official Google Photos API was restricted on 2025-03-31 to only allow downloading photos uploaded by your own app**, meaning gphotosdl is the de facto standard for downloading at original quality from photos uploaded by the Photos mobile app.
- Proposed change:
  - **Spike** (4-8 hours): try gphotosdl against the user's ChromeProfile_TV. Confirm:
    - Can it find and download a known chunk?
    - Does it handle the two-Google-accounts case via separate profile dirs?
    - Does parallel download work better than mainfetch's harvester loop?
  - **If yes**: refactor `mainfetch.py` to be a thin Python shim:
    1. Start gphotosdl as a subprocess pointed at the right ChromeProfile.
    2. HTTP-POST search queries; receive download URLs.
    3. Reuse MediaVault's hash-routing (`fetch_single_entry`'s harvester logic) on top of gphotosdl's downloads.
  - **If no** (e.g., gphotosdl assumes a single account): cherry-pick patterns:
    - Their session-expiry detection.
    - Their throttling / rate-limit handling against Google.
    - Their parallel-download orchestration.
    - Their cookie persistence and re-auth flow.
- Rationale: gphotosdl is maintained by the rclone team with broad community testing. Outsourcing the most fragile piece of MediaVault to a project that exists explicitly for it is a strategic win.
- Goal: Replace or harden the Selenium fetch path using lessons from a battle-tested alternative.
- Effort estimate: medium (spike) → large (full replacement if chosen)
- Status: pending

---

## IMP-G3: Borrow patterns from tdl (Telegram cold storage)

- Category: refactor
- Priority: low
- Files: future considerations for IMP-E10 Telegram dispatch, IMP-F10 status broadcaster
- Informs: IMP-A2 (CLI design), IMP-E10 (Telegram bot), IMP-F9 (multi-cloud)
- Current behavior: `tdl` (https://github.com/iyear/tdl) is a Go tool using Telegram as cold storage — same trick as MediaVault, different cloud. The Telegram-as-storage ecosystem is more mature than the Google Photos one and has converged on:
  - Single-binary deployment with zero Python runtime hassles (Go).
  - Parallel chunk uploads that saturate available bandwidth.
  - Export-to-JSON as the lingua franca for integrations.
  - Subcommand structure (`tdl chat`, `tdl forward`, `tdl up`, `tdl down`) — analogous to MediaVault's `cmd_*` shape.
- Proposed change:
  - **CLI shape inspiration**: tdl's subcommand structure validates argparse-with-subparsers (IMP-A2) as the right shape for MediaVault.
  - **Parallel uploads**: tdl saturates bandwidth across chunks. MediaVault's `cmd_push` is single-stream. With IMP-E7 (multi-device push) we get cross-device parallelism; tdl shows intra-device parallelism is also feasible.
  - **Future considerations**: if Google's policy ever forces a multi-cloud move (IMP-F9), tdl is a reference implementation for "Telegram as backend".
- Rationale: A second mature reference for the same architectural problem broadens the design space and validates approaches.
- Goal: Architectural ideas to borrow when designing IMP-A2, IMP-E10, IMP-F9.
- Effort estimate: small (research only)
- Status: pending

---

## IMP-G4: Build the Apple TV UI as a Jellyfin plugin instead of from scratch

- Category: refactor
- Priority: medium (long-term; depends on prerequisites)
- Files: future Jellyfin plugin project, separate from MediaVault repo
- Informs: [[project_future_apple_tv_ui]], IMP-E9, IMP-E12
- Current behavior: The future UI goal ([[project_future_apple_tv_ui]]) is "Apple TV-style smooth UI for browsing the archive". Building one from scratch (Electron/Tauri + React) is a multi-month project.
- Proposed change:
  - **Don't build a Jellyfin from scratch**. Build a small **Jellyfin plugin** (https://jellyfin.org) that:
    - Treats a `<1 KB` `.mkv` (MediaVault's dummy marker) as a special "archived" state in Jellyfin's UI.
    - Replaces the `[Play]` button on archived items with `[Restore]`.
    - Hits MediaVault's CLI (or its FastAPI wrapper from IMP-E12) to trigger `fetch_restore` when clicked.
    - Listens to the WebSocket status (IMP-F10) for live progress.
    - Calls Jellyfin's library-refresh API once the file is restored, then plays normally.
  - Reference plugins to study: `jellyfin-plugin-home-sections`, `Gelato`, `KefinTweaks` (https://github.com/awesome-jellyfin/awesome-jellyfin).
  - Jellyfin's default web UI is already polished and tile-driven. With a custom theme it looks Apple TV-ish. Clients like ARVIO (Android TV) and Infuse (Apple TV) consume Jellyfin servers directly — so a Jellyfin-plugin-based MediaVault would inherit Apple TV support for free if the user owns an Apple TV.
- Rationale: 95% of the UI work is already done by Jellyfin's team. MediaVault contributes the "archived/restore" semantic on top.
- Goal: Apple TV-style archive browser without building one from scratch.
- Effort estimate: medium (plugin development)
- Status: pending

---

## IMP-G5: Borrow restic / borgbackup dedup-block design for IMP-F2

- Category: refactor
- Priority: low (only if IMP-F2 is pursued)
- Files: future content-defined-chunking implementation
- Informs: IMP-F2 (differential dedup)
- Current behavior: IMP-F2 proposes content-defined chunking for dedup. Restic and borgbackup are the production-grade implementations of this for general backup, with well-understood trade-offs.
- Proposed change:
  - When (if) IMP-F2 is implemented, study restic's chunker:
    - Rolling-hash window size (typically 64 bytes).
    - Target chunk size (typically 1-8 MB for backup; would need re-tuning for ~9 GB media chunks).
    - Boundary-emission rule (low N bits of hash = 0).
    - Content-addressed store schema.
  - Borg uses a similar approach with a different hash function (BuzHash).
  - Both have published security analyses of their content-defined boundaries — important if combined with encryption (IMP-F1, since CDC boundaries can leak about file content via chunk-size analysis).
- Rationale: CDC is non-trivial to implement correctly. Two mature open-source reference implementations exist.
- Goal: Don't reinvent rolling-hash CDC when restic / borg already did it well.
- Effort estimate: small (research only)
- Status: pending
