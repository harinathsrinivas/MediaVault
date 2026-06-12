# Improvements — Tier F · Creative / Moonshot Features

> Bigger lifts, more speculative payoffs. Some are research projects in their own right (F2, F3, F8). Others are direct extensions of features that will exist after Tiers A–E (F6 needs E4 watch-state; F7 needs E1 subtitles). Don't budget for any of these until the core is solid.

> **Cross-cutting context:**
> - The user's archive is "public-facing personal media" — not state secrets, but also not zero-privacy. Google can technically see every chunk. The encryption discussion (F1) is about that.
> - The MediaVault system is currently single-user. F5 and F9 break that assumption.
> - The couch/Jellyfin end-goal lives in Tiers S/U + `docs/feature-fable-review/ROADMAP_END_GOAL.md`; F4/F6/F10 are deep/advanced versions of ideas that now have nearer-term homes there.
> - ⚠️ **THE CONTAINER CONSTRAINT (load-bearing, identified 2026-06-12):** today's mkvmerge-split chunks are *valid, independently playable video files* — which is exactly why the Pixel Photos app uploads them at all. Any scheme that produces NON-video artifacts (raw encrypted blobs F1, content-defined raw chunks F2, Reed-Solomon parity shards F3) likely **breaks Google Photos ingestion entirely** (the app only backs up media files it can recognize). Each of those tasks must first solve "wrap arbitrary bytes inside a Photos-acceptable video container" (e.g., embed payload as appended MKV attachments/padding in a tiny valid video, or steganographic NAL stuffing) — a research problem in its own right, logged in `docs/feature-fable-review/BLOCKERS_AND_MOONSHOTS.md`.
> - **Attribute key (added 2026-06-12):** `Risk` = blast radius of MAKING the change. `If skipped` = what stays impossible, with a scenario.

---

## IMP-F1: Encrypted-at-rest before chunking

- Category: security
- Priority: medium
- Files: new `mvcrypt.py`; modifies `cmd_push` (split + hash phase) and `cmd_restore` (merge phase)
- Current behavior: Chunks land in Google Photos as normal video files. Google's servers can technically index, scan, or analyze the content. For most home-video content the privacy floor is "Google sees a movie I legally ripped" — acceptable. For personal home videos, screen recordings, or anything sensitive: not acceptable.
- Proposed change:
  - Before splitting, encrypt the file with AES-CTR (streaming-friendly so it doesn't double-disk-cost).
  - Per-entry random key, stored LOCAL-ONLY: `~/.mediavault/keys/<short_id>.key` or a single master `keys.json` backed up to a password manager. NEVER uploaded next to the chunks.
  - On restore, decrypt after merge.
  - ⚠️ **Feasibility gate first (the container constraint):** an AES blob is not a valid video — the Pixel Photos app will likely refuse to upload it. Before ANY implementation, run a spike: wrap N MB of random bytes in a minimal valid MKV (attachment track or padding elements), confirm (a) Photos uploads it at "original quality", (b) the downloaded bytes round-trip EXACTLY (no transcode mangling). If exact round-trip fails, F1 is **blocked** on this backend (revisit under F9 multi-cloud, where real object stores accept arbitrary bytes).
  - Backwards-compat: existing un-encrypted chunks remain readable. New chunks encrypted only when `entry.encrypted: true`.
- Rationale: Privacy. Today's design trusts Google's terms-of-service. Encryption gives provable confidentiality.
- Goal: Optional per-entry encryption with secure key management. Default off; opt-in for sensitive content.
- Effort estimate: large (+ the wrapping spike)
- Risk: high — touches split, push, restore AND the integrity model (hashes would cover ciphertext; verify-or-bless semantics need an encrypted-variant story); key loss = permanent data loss, a NEW failure mode the system never had. Change-gate review required (restore-path changes).
- If skipped: cloud-side privacy stays at "trust Google" — acceptable for ripped commercial media, unacceptable if the vault ever holds personal footage; users must simply not vault sensitive content.
- Status: pending

---

## IMP-F2: Differential dedup via content-defined chunking

- Category: performance
- Priority: low
- Files: refactor `split_video_file`; add a chunking layer; entry schema additions
- Current behavior: Today's split is size-based. If the user re-rips a movie at a different quality, the chunks bear no relation to the previous rip — 100% of bytes are re-uploaded.
- Proposed change:
  - Replace size-based splitting with **content-defined chunking** (rolling hash, like restic / borg / rclone's chunker): boundaries where the rolling hash matches a pattern; variable chunk size centered on ~9 GB.
  - Maintain a global content-addressed store: chunk SHA256 → which entries reference it.
  - Re-rip detection: when pushing a new entry, if a chunk hash matches an already-uploaded chunk, SKIP it (reference it in the new entry's split_info).
  - Trade-off: gives up `mkvmerge`-as-splitter; restore becomes byte-concatenation (which would, ironically, give byte-identical restores and make the PR #20 canonical-hash machinery unnecessary for these entries).
  - ⚠️ **Same container constraint as F1**: raw CDC chunks are not valid videos → Photos likely won't upload them. Blocked on the same wrapping spike. Honestly assessed: for a single-user vault of mostly-unique rips, dedup wins are small (different encodes share almost no bytes — video codecs destroy content-level similarity); the real prize here would be byte-identical restores, which the deterministic-merge canonical hash (PR #20) already 90%-solved another way.
- Rationale: Storage savings on re-rips + restic-style byte-identical restore.
- Goal: Re-rip uploads only the changed bytes. Library scales sub-linearly with re-rip count.
- Effort estimate: large
- Risk: high — replaces the most battle-tested subsystem (balanced split) and the entire restore identity model; every existing archived entry would live under a legacy scheme alongside the new one.
- If skipped: re-rips re-upload fully (rare event, unlimited uploads → near-zero practical cost). Honestly: skipping this is the right call unless F9 lands on a real object store first.
- Status: pending

---

## IMP-F3: Erasure-coded redundancy across multiple cloud accounts

- Category: security
- Priority: low
- Files: refactor push/restore pipelines; new `mvfec.py`; entry schema for multi-account chunk placement
- Current behavior: All chunks of an entry live in ONE Google account. Account ban / deletion / hack → total loss of that entry's data (the local copy is a dummy by then).
- Proposed change:
  - Reed-Solomon erasure coding: N data chunks + M parity chunks; any N of (N+M) reconstructs.
  - Spread the (N+M) chunks across multiple Google accounts (the 2-account split already exists; the 4-Pixel topology answer may yield more) or other clouds (F9).
  - Lose one account → full reconstruction from the rest.
  - ⚠️ **Parity shards hit the container constraint** (not valid videos). Cheaper interim that needs NO new tech: **plain replication of the data chunks to a second account** (push the same chunks via a second phone/account; record both locations in split_info). 2× upload time, zero new failure modes, survives account loss. Recommend replication-first; erasure coding only if account count grows past 3-4.
- Rationale: For users whose archive is genuinely irreplaceable. Account loss is a real failure mode worth mitigating (and the ToS-gray storage trick makes it likelier than for normal users — see RESEARCH_STORAGE_STREAMING §1.3).
- Goal: Survive the loss of any single cloud account.
- Effort estimate: large (erasure variant) / medium (replication variant)
- Risk: high (erasure) / medium (replication — extends push bookkeeping and fetch-side account routing, both well-understood seams).
- If skipped: a single Google account termination silently destroys every entry homed there; with ~412 archived entries across 2 accounts, that's a ~50% library-loss event with no recovery path beyond re-ripping.
- Status: pending

---

## IMP-F4: Streamable restore (play while still downloading)

- Category: other
- Priority: low (near-term experiments live in IMP-S6; this entry holds the deep version)
- Files: refactor split algorithm to emit a "primer" first chunk; refactor restore/serving
- Current behavior: Restore is all-or-nothing — every chunk must arrive before mkvmerge runs. For a 70 GB movie that's an hour-long wait before playback can begin.
- Proposed change (UPDATED 2026-06-12 with the research findings — `RESEARCH_STORAGE_STREAMING.md` §2):
  - Key insight: **mkvmerge-split chunks are ALREADY independently playable** — no special primer needed for "watch the first chunk now" (T2). What a primer adds is latency control: a deliberately small chunk 1 (e.g., first ~10 min / 1-2 GB) makes time-to-first-frame minutes instead of ~15+.
  - Tiering: T2 (serve chunk 1 while 2..N download; hand off via Kodi playlist or a growing pre-merge file) → T3 (gphotosdl-style local proxy streaming the original WHILE it downloads, `.strm`-to-localhost items). Full design + client-behavior caveats in the research doc.
  - Integrity contract unchanged: the final deterministic merge + verify-or-bless still runs after all chunks arrive; early playback is a PREVIEW path, never the archival path.
- Rationale: Eliminates the biggest UX friction of an on-demand archive system.
- Goal: Start watching within minutes of triggering an unarchive.
- Effort estimate: large
- Risk: high — touches split sizing (primer chunk changes the balanced-split invariants), restore sequencing, and adds a serving surface; must NOT alter the restore PONR/journal contract (change-gate review) — early-play must be strictly read-only on the restore pipeline.
- If skipped: select-to-play latency stays = full fetch time; smart prefetch (IMP-S5) hides this for episodic bingeing but not for spontaneous movie picks.
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
- Rationale: Family of MediaVault users. One household, two PCs, shared archive. (Also: a free, versioned off-machine backup of the library JSONs — valuable even single-PC.)
- Goal: Multiple PCs see the same library state. Either can push or unarchive.
- Effort estimate: medium
- Risk: medium — a bad auto-merge of library JSON is a data-integrity event; single-PC mode (commit+push only, no concurrent writer) is risk-free and delivers the backup benefit alone. Recommend shipping the backup half first.
- If skipped: the three JSONs' only protection remains atomic-save + manual `library - Copy.json` snapshots on the SAME disk; a disk failure loses the library (sidecars/mvmeta allow slow reconstruction — see IMP-C10).
- Status: pending

---

## IMP-F6: Smart cold-tier pruning

- Category: other
- Priority: low (CORE POLICY ABSORBED INTO IMP-S5 — this entry keeps the advanced policy-engine ideas)
- Files: new `cmd_prune_by_policy`; depends on IMP-E4 watch-state
- Current behavior: Replace is manual. The user decides what to archive based on intuition.
- Proposed change:
  - The simple, high-value 80% — *watched → grace period → auto-archive*, and *watching N → prefetch N+1* — now ships as the **Tier S daemon policy (IMP-S5)** per the 2026-06-12 roadmap. This entry holds the long-tail policy engine:
    - "Archive anything not watched in 12 months."
    - "Archive any 4K HDR file when local disk drops below 200 GB free."
    - "Always keep watched-this-month items local."
    - Calendar-aware prefetch ("holiday next week → prefetch the Marvel collection").
  - Config-driven (`policies.yml` under IMP-A5), dry-run-first, every action logged + surfaced in-client.
- Rationale: Automates local-vs-cloud tiering beyond the built-in S5 defaults.
- Goal: The local archive automatically rightsizes to watching patterns under arbitrary user policies.
- Effort estimate: medium (on top of S5)
- Risk: medium — policy bugs translate directly into unwanted `replace` operations (destructive by design); S5's grace-period + Keep-collection guardrails must apply to every policy action.
- If skipped: S5's built-in defaults cover the binge loop; only exotic policies stay manual.
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
  - Combined with IMP-F4/S6 early playback: jump straight to the matched timestamp.
- Rationale: Search your entire library by dialogue. Massive utility, tiny implementation.
- Goal: "Find me the scene where someone says X" works across the whole archive.
- Effort estimate: small (assuming subs already extracted)
- Risk: low — read-only over extracted text files.
- If skipped: dialogue-recall searches stay impossible (the content is either archived or unindexed).
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
  - Heavy GPU cost (the Alienware RTX makes it feasible); one-time per entry; cached forever. Natural hook: run during the post-restore enrichment window (alongside trickplay/intro-fingerprint generation, IMP-U2) while the real bytes are local.
- Rationale: Crazy power for finding "that scene I half-remember". Frontier-of-what's-possible feature.
- Goal: Semantic search across visual + audio content of the entire archive.
- Effort estimate: large
- Risk: low-medium — purely additive analysis pipeline; main cost is GPU time and embedding-store maintenance.
- If skipped: semantic recall stays unavailable; F7's literal-text grep is the fallback.
- Status: pending

---

## IMP-F9: Multi-cloud backend abstraction

- Category: refactor
- Priority: low (strategic insurance — revisit YEARLY or immediately upon any Google policy signal)
- Files: refactor `cmd_push` ADB-specific bits into a `Backend` interface; new backends
- Current behavior: ADB→Pixel→Google Photos is hardwired throughout. If Google ever ends the Pixel-1 unlimited grandfather or blocks web-automation fetch, MediaVault has no plan B (RESEARCH_STORAGE_STREAMING §1.3 risk register).
- Proposed change:
  - Define a `Backend` ABC: `push(chunk, remote_path)`, `download(remote_path, local_path)`, `list_remote(prefix)`, `delete(remote_path)`.
  - Implementations: `GooglePhotosViaPixelBackend` (today's behaviour), then rclone-backed targets (Mega/OneDrive/Dropbox/B2 via `rclone rcd` or subprocess — one integration, forty providers, crypt for free).
  - Each entry records the `backend` it was pushed via; restore picks the matching one.
  - Mixes with F3: per-chunk backend placement. Real object stores also dissolve the container constraint that blocks F1/F2/F3 — encrypted/raw chunks are fine there.
- Rationale: Insurance against the single existential platform risk. The fact that F1/F2/F3 are all partially blocked by Google-Photos-specific constraints makes this the strategic unlock for the whole advanced-storage family.
- Goal: MediaVault is no longer locked to Google Photos; sensitive or critical content can live on paid-but-honest storage.
- Effort estimate: large
- Risk: high — refactors the push/fetch core into an abstraction; mitigate by keeping `GooglePhotosViaPixelBackend` as a pure extraction of today's code (byte-identical behavior) before adding any second backend.
- If skipped: a single Google policy change (bot detection on the web session, grandfather revocation) turns the entire vault read-only-at-best overnight, with Takeout as the only (slow, bulk) evacuation route.
- Status: pending

---

## IMP-F10: WebSocket live-status broadcaster

- Category: refactor
- Priority: low (DELIVERY VEHICLE CHANGED — becomes the event bus inside the Tier S daemon / IMP-E12 web UI)
- Files: refactor `cmd_*` to emit progress events; daemon WebSocket/SSE endpoint
- Current behavior: Progress is only on stdout of the running shell. No way to consume it from another process / UI / phone.
- Proposed change:
  - Refactor `cmd_*` to emit progress events to a queue (callback/event-emitter seam injected alongside IMP-A3's logging — do them together: every log record IS a progress event).
  - The Tier S daemon exposes the stream over WebSocket/SSE at its existing port; the E12 web UI subscribes for live progress bars; in-client surfaces (IMP-S3) consume digested versions (collection updates, DisplayMessage milestones).
- Rationale: Decouples progress emission from terminal output. Foundation for any real-time UI.
- Goal: Real-time progress consumable by any client.
- Effort estimate: medium
- Risk: medium — threading an event seam through long-running loops (push chunk loop, harvester) touches hot paths; emit-and-forget with a bounded queue so a slow consumer can never stall a push.
- If skipped: the web UI polls instead of streaming (fine at small scale); in-client progress stays at the coarse collection/message granularity.
- Status: pending
