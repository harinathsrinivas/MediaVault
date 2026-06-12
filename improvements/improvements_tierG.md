# Improvements — Tier G · Lessons From Similar Projects

> Cross-cutting design lessons drawn from production-grade adjacent projects. These are not standalone tasks to implement directly — they are inputs that should shape decisions in Tiers A–F/S/U. Each lesson lists which task it most strongly informs. The 2026-06-12 research dossier (`RESEARCH_STORAGE_STREAMING.md` §3) extends this tier with the debrid-stack / *arr / JellyBridge / Seerr pattern catalog.

> **Cross-cutting context:**
> - MediaVault sits in a small but real ecosystem of "split-large-files-and-upload-to-free-cloud-storage" tools. rclone (chunker + gphotosdl) and tdl (Telegram) are the most mature.
> - Google Photos API policy as of **March 31, 2025**: third-party apps can only download photos that they themselves uploaded. Since the user's Pixel phone Google Photos app uploads them (not a MediaVault API client), the official API is permanently unavailable for restore. **Browser-session automation is the only path forward** (verified again 2026-06-12).
> - Jellyfin is the chosen couch platform (2026-06-12 session decision) — see Tier S/U and `ROADMAP_END_GOAL.md`.
> - **Attribute key (added 2026-06-12):** `Risk` = blast radius of acting on the lesson. `If skipped` = what we keep re-learning the hard way.

---

## IMP-G1: Adopt rclone chunker patterns for push reliability

- Category: refactor
- Priority: medium
- Files: `cmd_push`, entry `split_info` schema, remote upload paths on phone
- Informs: IMP-A1 (mvcommon), IMP-C8 (post-push verify), IMP-C2 (retry)
- Current behavior (pre-fix): bespoke split+push with no partial-upload protection and no remote-side recovery metadata.
- Proposed change: `.partial` upload + atomic `adb shell mv` rename; remote `<base> [<uid>].mvmeta.json` sidecar mirroring split_info; chunk-filename overhead audit.
- Rationale: rclone solved partial-upload-looks-complete and rebuild-from-remote in production.
- Goal: Partial uploads never observable as complete; remote-side recovery possible without the local library.
- Effort estimate: medium
- Status: done (PR #7 shipped `.partial`+mv; the mvmeta remote sidecar shipped with the rollback-era work — both verified in current code 2026-06-12. Remaining unshipped sub-item: the **260-char Windows path / 255-char filename overhead audit** for long titles + ` [uid].chunk.NNN.mkv` suffixes — small, folded into IMP-D4's verify_library checks as a path-length lint.)

---

## IMP-G2: Evaluate replacing mainfetch.py with rclone's gphotosdl

- Category: refactor
- Priority: high
- Files: potentially replace `mainfetch.py` (491 lines) with a thin wrapper around external `gphotosdl` binary
- Informs: IMP-C5 (fallback search), IMP-C6 (session expiry), Tier S fetch hardening (IMP-S7), T3 proxy-streaming moonshot (RESEARCH_STORAGE_STREAMING §2)
- Current behavior: `mainfetch.py` is a hand-rolled Selenium driver. It works for the user's setup. But it is the most fragile piece of MediaVault — every Google Photos UI change can break it. rclone's project ships **gphotosdl** (https://github.com/rclone/gphotosdl), a Go binary that runs a headless Chrome and exposes a **local HTTP proxy** for original-quality downloads (`http://localhost:8282/id/<photoID>`), actively maintained because the post-2025-03-31 API only allows self-uploaded content — gphotosdl is the de facto standard for downloading originals that the Photos mobile app uploaded.
- Proposed change:
  - **Spike** (4-8 hours): run gphotosdl against a COPY of ChromeProfile_TV. Confirm:
    - Can it find and download a known chunk (search-by-filename or id discovery path)?
    - Does it handle the two-Google-accounts case via separate profile dirs / two instances on two ports?
    - Does its download-streaming proxy behave under our chunk sizes (9.6 GB)? (This doubles as the T3 streaming feasibility probe — it streams bytes through as they download.)
    - Parallelism vs mainfetch's trigger+harvester?
  - **If yes**: refactor `mainfetch.py` into a thin shim — start gphotosdl per profile, resolve photo ids, GET from the proxy, keep MediaVault's hash-routing as the integrity layer.
  - **If no**: cherry-pick its session-expiry detection, throttling/rate-limit handling, parallel-download orchestration, cookie persistence/re-auth flow into mainfetch (feeds IMP-C5/C6 and IMP-S7's CDP migration).
- Rationale: Outsourcing the most fragile piece of MediaVault to a project that exists explicitly for this problem — maintained by the rclone team with broad community testing — is a strategic win; and its proxy architecture is the seed of watch-while-fetching.
- Goal: Replace or harden the Selenium fetch path using a battle-tested alternative; produce a written go/no-go decision.
- Effort estimate: medium (spike) → large (full replacement if chosen)
- Risk: medium — the spike itself is read-only/sandboxed (use a copied Chrome profile so the live session cookies aren't risked); a full replacement swaps the fetch engine and must preserve the hash-routing contract and the two-profile routing exactly.
- If skipped: mainfetch remains one Google UI redesign away from total fetch outage, with selectors only one person maintains; every Photos frontend experiment Google runs is a potential 90-minute debugging night.
- Status: pending

---

## IMP-G3: Borrow patterns from tdl (Telegram cold storage)

- Category: refactor
- Priority: low
- Files: future considerations for IMP-E10 Telegram dispatch, IMP-F9 multi-cloud, IMP-F10 status broadcaster
- Informs: IMP-A2 (CLI design), IMP-E10 (Telegram bot), IMP-F9 (multi-cloud)
- Current behavior: `tdl` (https://github.com/iyear/tdl) is a Go tool using Telegram as cold storage — same trick as MediaVault, different cloud. The Telegram-as-storage ecosystem is more mature than the Google Photos one and has converged on:
  - Single-binary deployment with zero Python runtime hassles (Go).
  - Parallel chunk uploads that saturate available bandwidth.
  - Export-to-JSON as the lingua franca for integrations.
  - Subcommand structure (`tdl chat`, `tdl forward`, `tdl up`, `tdl down`) — analogous to MediaVault's `cmd_*` shape.
- Proposed change:
  - **CLI shape inspiration**: tdl's subcommand structure validates argparse-with-subparsers (IMP-A2) as the right shape for MediaVault.
  - **Parallel uploads**: tdl saturates bandwidth across chunks. MediaVault's `cmd_push` is single-stream. With IMP-E7 (multi-device push) we get cross-device parallelism; tdl shows intra-device parallelism is also feasible (less relevant for ADB-over-USB, which is link-bound).
  - **Future considerations**: if Google's policy ever forces a multi-cloud move (IMP-F9), Telegram-as-backend (via tdl patterns or tdl itself) is a candidate with REAL arbitrary-file support — no container constraint (Tier F header) because Telegram stores files, not "photos".
- Rationale: A second mature reference for the same architectural problem broadens the design space and validates approaches.
- Goal: Architectural ideas to borrow when designing IMP-A2, IMP-E10, IMP-F9.
- Effort estimate: small (research only)
- Risk: low — research input only.
- If skipped: design decisions in A2/E10/F9 are made with one fewer production reference.
- Status: pending

---

## IMP-G4: Build the couch UI on Jellyfin instead of from scratch

- Category: refactor
- Priority: medium → **high (now the confirmed direction — 2026-06-12 session decision: Jellyfin-first)**
- Files: future Jellyfin plugin project (separate from the MediaVault repo) + the Tier S daemon (this repo)
- Informs: [[project_future_apple_tv_ui]], IMP-E9, IMP-E12, all of Tier S/U
- Current behavior: The couch-UI goal is "Apple TV-style smooth UI for browsing the archive". Building one from scratch (Electron/Tauri + React, or a native tvOS app) is a multi-month project.
- Proposed change (UPDATED 2026-06-12 — this lesson GRADUATED into the roadmap):
  - **Don't build a UI from scratch — ride Jellyfin.** The original analysis stands and the session decision confirmed it. Two refinements from the research (`RESEARCH_MEDIA_SERVERS.md`):
    1. **The daemon-first path gets ~90% of the experience with ZERO client/plugin code** (IMP-S1..S5): dummies are already real playable items, so "play a dummy" = the fetch request (webhook-observed), DisplayMessage = the notify, collections = the status rows, grace-period policy = the archive prompt. The C# plugin is the *polish* phase, not the entry fee.
    2. **The plugin's old detection design is stale**: dummies are no longer `<1 KB` text blobs with an `Original Hash:` marker — they are ~10 KB valid videos (PR #1/#3). Detection = size < `DUMMY_MAX_BYTES` (200 KB) + `uid` sidecar / daemon API lookup. `apple_tv_ui_roadmap.md` §5 must be read with this correction (banner added 2026-06-12).
  - Reference plugins to study when the plugin phase arrives: `jellyfin-plugin-home-sections`, JellyBridge (placeholder-items-as-actions precedent), Intro Skipper (Media Segments usage), the plugin-template repo.
- Rationale: 95% of the UI work is already done by Jellyfin's team and clients (Swiftfin/Infuse/Kodi). MediaVault contributes the "archived/restore" semantic on top.
- Goal: Apple TV-style archive browsing without building a UI — via the staged plan in `ROADMAP_END_GOAL.md` (daemon → conventions → plugin polish).
- Effort estimate: medium (plugin phase; the daemon phases are tracked in Tier S)
- Risk: low as a direction (everything is additive around the untouched CLI core); plugin-phase risk is Jellyfin plugin-ABI churn across server versions — pin to an LTS line when that phase starts.
- If skipped: the couch goal requires either building a client from scratch (months) or accepting Plex's closed surface (impossible — no plugin API); skipping Jellyfin means effectively skipping the end goal.
- Status: pending (direction locked; execution tracked as Tier S/U + ROADMAP_END_GOAL phases)

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
  - Remember the Tier F container constraint: restic/borg chunks are raw blobs — fine on real object stores (F9), blocked on Google Photos without a wrapping layer.
- Rationale: CDC is non-trivial to implement correctly. Two mature open-source reference implementations exist.
- Goal: Don't reinvent rolling-hash CDC when restic / borg already did it well.
- Effort estimate: small (research only)
- Risk: low — research input only.
- If skipped: only matters if F2 proceeds; then skipping = reinventing a subtle wheel.
- Status: pending
