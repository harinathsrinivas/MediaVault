# Improvements — Tier F · Creative / Moonshot Features

> Bigger lifts, more speculative payoffs. Some are research projects in their own right (F2, F3, F8). Others are direct extensions of features that will exist after Tiers A–E (F6 needs E4 watch-state; F7 needs E1 subtitles). Don't budget for any of these until the core is solid.

> **Cross-cutting context:**
> - The user's archive is "public-facing personal media" — not state secrets, but also not zero-privacy. Google can technically see every chunk. The encryption discussion (F1) is about that.
> - The MediaVault system is currently single-user. F5 and F9 break that assumption.
> - The Apple TV UI ([[project_future_apple_tv_ui]]) is captured as a separate future task; F10 is a supporting feature for it.
> - `mkvmerge` re-muxes during split — chunks are independently playable. Encryption (F1) destroys that property; treat it as a deliberate trade-off, not a regression.

---

## IMP-F1: Encrypted-at-rest before chunking

- Category: security
- Priority: medium
- Files: new `mvcrypt.py`; modifies `cmd_push` (around line 595-617 the split + hash phase) and `cmd_restore` (around the merge phase)
- Current behavior: Chunks land in Google Photos as normal video files. Google's servers can technically index, scan, or analyze the content. For most home-video content the privacy floor is "Google sees a movie I legally ripped" — acceptable. For personal home videos, screen recordings, or anything sensitive: not acceptable.
- Proposed change:
  - Before splitting, encrypt the file in-place with AES-CTR (streaming-friendly so it doesn't double-disk-cost).
  - Per-entry random key. Key stored in:
    - Option A: `<short_id>.key` sidecar next to the chunks (defeats the purpose if Google has both — DON'T do this).
    - Option B: `~/.mediavault/keys/<short_id>.key` — local-only, never uploaded.
    - Option C: A single master `~/.mediavault/keys.json` keyed by `short_id`. Backed up to a password manager.
  - On restore, decrypt after merge.
  - TRADE-OFF: Chunks become opaque blobs to Google Photos. They still upload (Photos accepts any "image/video" MIME) but the "click a chunk and preview" property is gone. Search-by-content (Photos' face/object search) doesn't work on encrypted data.
  - Backwards-compat: existing un-encrypted chunks remain readable. New chunks are encrypted only when `entry.encrypted: true` is set.
- Rationale: Privacy. Today's design trusts Google's terms-of-service. Encryption gives provable confidentiality.
- Goal: Optional per-entry encryption with secure key management. Default off; opt-in for sensitive content.
- Effort estimate: large
- Status: pending

---

## IMP-F2: Differential dedup via content-defined chunking

- Category: performance
- Priority: low
- Files: refactor `split_video_file` (189-262); add a chunking layer; entry schema additions
- Current behavior: Today's split is size-based. If the user re-rips a movie at a different quality, the chunks bear no relation to the previous rip — 100% of bytes are re-uploaded.
- Proposed change:
  - Replace size-based splitting with **content-defined chunking** (rolling hash, like restic / borg / rclone's chunker):
    - Walk the file with a rolling hash window.
    - Emit a chunk boundary whenever the hash matches a pattern (e.g., low N bits = 0).
    - Chunks have variable size centered on a target (~9 GB to match current behaviour).
  - Maintain a global content-addressed store: chunk SHA256 → which entries reference it.
  - Re-rip detection: when pushing a new entry, hash potential chunks first; if any match an already-uploaded chunk, SKIP that chunk (just reference it in the new entry's split_info).
  - Trade-off: gives up `mkvmerge`-as-splitter. Chunks would be raw bytes, not playable. Restore becomes "fetch the right raw chunks → concatenate → file is byte-identical to original". That actually FIXES the hash-divergence problem mentioned in [[feedback_mkvmerge_hash_divergence]] but at the cost of "each chunk is independently playable in Google Photos".
- Rationale: Dramatic storage savings if the user has multiple cuts/remasters/encodes of the same content. Also enables the byte-identical restore property restic-style.
- Goal: Re-rip uploads only the changed bytes. Library scales sub-linearly with re-rip count.
- Effort estimate: large
- Status: pending

---

## IMP-F3: Erasure-coded redundancy across multiple cloud accounts

- Category: security
- Priority: low
- Files: refactor push/restore pipelines; new `mvfec.py`; entry schema for multi-account chunk placement
- Current behavior: All chunks of an entry live in ONE Google account. Account ban / deletion / hack → total loss of that entry's data.
- Proposed change:
  - Use Reed-Solomon erasure coding: split a file into N data chunks + M parity chunks. Any N out of (N+M) survives reconstruction.
  - Spread the (N+M) chunks across multiple Google accounts (or other clouds — see F9).
  - Lose one account → full reconstruction from the remaining accounts' chunks.
  - Trade-off: complex. Adds parity-compute overhead. Requires multiple cloud accounts on hand.
- Rationale: For users whose archive is genuinely irreplaceable. Account loss is a real failure mode worth mitigating.
- Goal: Survive the loss of any single cloud account.
- Effort estimate: large
- Status: pending

---

## IMP-F4: Streamable restore (play while still downloading)

- Category: other
- Priority: low
- Files: refactor split algorithm to emit a "primer" first chunk; refactor restore to pipe to mpv
- Current behavior: Restore is all-or-nothing — every chunk must arrive before mkvmerge runs. For a 70 GB movie that's an hour-long wait before playback can begin.
- Proposed change:
  - At split time, emit a SMALL first chunk (e.g., 200 MB) containing the MKV header and the first few minutes of video. This chunk is independently playable.
  - At fetch time, prioritize this primer chunk first. As soon as it arrives, launch mpv pointed at the primer.
  - As later chunks arrive, append to a growing "running file" via mkvmerge in incremental mode, or stitch on-the-fly via mpv's playlist mode.
  - Achieves "watch within 2 minutes of starting unarchive" instead of "watch within 1 hour".
- Rationale: Eliminates the biggest UX friction of an on-demand archive system.
- Goal: Start watching within minutes of triggering an unarchive.
- Effort estimate: large
- Status: pending

---

## IMP-F5: Cross-PC family library sync via git

- Category: other
- Priority: low
- Files: hooks around `save_library`; new `cmd_sync` command
- Current behavior: The library JSONs are local to one PC. A second household PC wanting to use MediaVault would have a separate library. Two PCs could both push to the same Google Photos and both could fetch, but they'd duplicate the bookkeeping.
- Proposed change:
  - After every `save_library`, optionally `git add C:\Media\library_*.json && git commit && git push` to a private remote repo.
  - On startup, `git pull` to grab any updates from the other PC.
  - Use git's conflict resolution for simultaneous edits (rare — JSON merges are usually clean given the per-key entries).
  - Sidecar files (`uid`, `<short_id>.sha256`, `checksums/*`) need not sync — they're recomputable.
- Rationale: Family of MediaVault users. One household, two PCs, shared archive.
- Goal: Multiple PCs see the same library state. Either can push or unarchive.
- Effort estimate: medium
- Status: pending

---

## IMP-F6: Smart cold-tier pruning

- Category: other
- Priority: low
- Files: new `cmd_prune_by_policy`; depends on IMP-E4 watch-state
- Current behavior: Replace is manual. The user decides what to archive based on intuition.
- Proposed change:
  - Policy-driven pruning. Examples:
    - "Archive anything not watched in 12 months" — sets `replace` for stale local files.
    - "Archive any 4K HDR file when local disk drops below 200 GB free".
    - "Always keep watched-this-month items local".
  - Reverse: `prefetch` — restore items matching upcoming-watch-list (e.g., "next week is a holiday, prefetch my Marvel collection").
  - Config-driven (`policies.yml` under IMP-A5).
- Rationale: Automates the local-vs-cloud tier decisions. Frees the user from monitoring disk space.
- Goal: The local archive automatically rightsizes to the user's watching patterns.
- Effort estimate: medium
- Status: pending

---

## IMP-F7: Subtitle-grep across entire archive

- Category: other
- Priority: medium (if E1 done)
- Files: new `cmd_grep_subs`; depends on IMP-E1 subtitle pre-extraction
- Current behavior: With subtitles pre-extracted (IMP-E1), they sit on disk but are never queried in aggregate.
- Proposed change:
  - New `python main.py grep_subs "I'll be back"` searches every `.srt` file under `C:\Media\` and returns matches with: entry ID, timestamp in file, surrounding line.
  - For SSA / ASS / VTT formats too.
  - `--lang en` filter, `--limit 20`.
  - Combined with IMP-F4 streamable restore: jump straight to the matched timestamp.
- Rationale: Search your entire library by dialogue. Massive utility, tiny implementation.
- Goal: "Find me the scene where someone says X" works across the whole archive.
- Effort estimate: small (assuming subs already extracted)
- Status: pending

---

## IMP-F8: AI tagging via CLIP / Whisper

- Category: other
- Priority: low
- Files: new `mvai.py` module; new commands `tag` and `search_semantic`
- Current behavior: Search is text-substring on filenames and metadata. There's no semantic understanding.
- Proposed change:
  - Two layers:
    - **Whisper** transcribes audio tracks (when subtitles aren't available). Stores transcript alongside subs.
    - **CLIP** extracts visual embeddings from sampled frames (e.g., every 5 minutes). Stores per-entry embedding.
  - New `python main.py search_semantic "spaceship explodes"` queries CLIP embeddings, returns matching entries + timestamps.
  - Heavy CPU/GPU cost. One-time per entry; cached forever.
- Rationale: Crazy power for finding "that scene I half-remember". Frontier-of-what's-possible feature.
- Goal: Semantic search across visual + audio content of the entire archive.
- Effort estimate: large
- Status: pending

---

## IMP-F9: Multi-cloud backend abstraction

- Category: refactor
- Priority: low
- Files: refactor `cmd_push` ADB-specific bits into a `Backend` interface; new `MegaBackend`, `OneDriveBackend`, `DropboxBackend`
- Current behavior: ADB→Pixel→Google Photos is hardwired throughout. If Google ever cracks down on the auto-upload-of-arbitrary-folders trick, MediaVault has no plan B.
- Proposed change:
  - Define a `Backend` ABC with methods `push(chunk, remote_path)`, `download(remote_path, local_path)`, `list_remote(prefix)`, `delete(remote_path)`.
  - Implementations: `GooglePhotosViaPixelBackend` (today's behaviour), `MegaBackend` (via mega.py SDK), `OneDriveBackend` (Microsoft Graph), `DropboxBackend`.
  - Each entry records `backend` it was pushed via; restore picks the matching one.
  - Future: per-account, per-chunk backend selection (mixes with F3 erasure coding).
- Rationale: Insurance against Google policy changes. Also opens the door to cheaper backends per content type.
- Goal: MediaVault is no longer locked to Google Photos.
- Effort estimate: large
- Status: pending

---

## IMP-F10: WebSocket live-status broadcaster

- Category: refactor
- Priority: low
- Files: refactor `cmd_*` to emit progress events; new `mvstatus.py` daemon
- Current behavior: Progress is only on stdout of the running shell. No way to consume it from another process / UI / phone.
- Proposed change:
  - Refactor `cmd_*` to emit progress events to a queue.
  - Optional sidecar daemon `mvstatus.py` exposes those events over WebSocket at `ws://localhost:8765/status`.
  - The future web UI (IMP-E12) and Apple TV UI subscribe to this stream for live progress bars during long ops.
  - Optional: Telegram bot (IMP-E10) tails the same stream and posts updates to the user's chat.
- Rationale: Decouples progress emission from terminal output. Foundation for any real-time UI.
- Goal: Real-time progress consumable by any client.
- Effort estimate: medium
- Status: pending
