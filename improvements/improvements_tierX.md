# Tier X — Cloud Resilience & Privacy (redundancy + anti-scanning)

> **Added 2026-06-12 (fable-review session, follow-up).** The user flagged that today's
> storage is a **single point of failure** (3 Google accounts — one each for movies / series /
> anime — multiple Pixel devices, but each piece of content lives in exactly ONE account) and
> asked for (a) ways to make it super robust (backup accounts, Google Photos sharing), and
> (b) encrypt-before-upload so automated content scanning can't flag the files. This tier holds
> both, grounded in 2026 research (sources at the bottom).
>
> **Why this jumped in urgency:** research found a **Feb 2026 wave of Google account bans from
> CSAM-AI false positives** — instant disable, NCMEC report, *no effective recourse*, and Google
> "almost never reverses the ban." For a vault whose only copies of ~412 archived titles live in
> three accounts, a single false-positive ban = catastrophic, unrecoverable loss. Redundancy here
> is no longer a moonshot; it's insurance against a documented, rising failure mode.
>
> **Standing constraints:** the 4× Pixel 1 XL unlimited original-quality upload path is sacred;
> any redundancy scheme must keep ALL copies on that free-unlimited path (not normal paid quota).
> Anything touching split/push/restore brushes the rollback change-gate.
>
> **Attribute key:** `Risk` = blast radius of MAKING the change. `If skipped` = the failure that
> persists, with a scenario.

---

## 0. Research summary — the backup-mechanism decision (answers the user's exact questions)

**Q: "If the main account is gone, will the backup (via Google Photos) also be removed from the
other accounts? Or do we need to manually put copies into multiple backup accounts?"**

| Backup mechanism | Survives owner-account loss? | Storage cost on backup | Keeps Pixel free-unlimited? | Verdict |
|---|---|---|---|---|
| **View-only shared album** | ❌ NO — when the owner account is deleted/banned, the shared album and its contents **disappear for everyone** (ownership-based; Google has no ownership transfer) | n/a | n/a | **Useless as backup** |
| **Recipient "Save to library" / Partner Sharing saved copy** | ✅ YES — the saved copy is independent and survives | ⚠️ Counts against the backup account's **normal quota** the moment sharing stops/owner is gone (the copy "wakes up" and occupies storage) | ❌ NO — a *saved* copy is a server-side copy at normal quota; only photos a **Pixel device uploads** get the original-quality-unlimited benefit | Works, but expensive & loses the free-unlimited trick |
| **Direct re-upload to each backup account via a Pixel signed into it** (RECOMMENDED) | ✅ YES — fully independent copy, full control | ✅ Free-unlimited (it IS a Pixel upload) | ✅ YES | **The correct backup for this system** |

**Conclusion:** sharing is **not** a real backup for this use case — relying on it means the backup
vanishes exactly when you need it (owner banned). The only mechanism that yields an independent,
free-unlimited, survives-anything copy is **MediaVault pushing the same chunks to a second/third
account via a Pixel logged into that account** — which is just multi-account replication
(builds on IMP-E7 multi-device push + IMP-F3 replication). Sharing is documented below (X2) only
as a niche secondary option with its exact caveats.

**Q: "Can we encrypt and upload so that if Google starts seeing into our files, it won't be caught
for copyright and still work as expected?"** — Yes, technically (X3), with one mandatory feasibility
spike. Key facts: Google Photos **Original Quality preserves bytes exactly** (MediaVault already
proves this via the deterministic-merge hash round-trip), so an encrypted payload wrapped inside a
valid video container would round-trip byte-exact; Matroska **attachments** can carry arbitrary
binary data inside a valid `.mkv`. The content-scanning (copyright hash-match + CSAM AI) operates on
**decoded video frames**, so a black-frame cover video carrying the real (encrypted) payload as an
attachment shows the scanners nothing to match. The spike must confirm the Pixel uploader *accepts*
and *byte-preserves* a small-video / huge-attachment MKV. See X3 for the full design + risks.

---

## IMP-X1: Multi-account chunk replication (the real backup)

- Category: security / robustness
- Priority: high
- Files: `cmd_push` (upload loop + `split_info` schema), `mainfetch` (fetch-side account routing), entry schema (`replicas`)
- Current behavior: every chunk of an entry lives in exactly ONE account (`mov-*`→movies acct, `tv-*`→series acct, `ani-*`→anime acct). The local copy is a dummy after `replace`. A single account ban/deletion/hack → **total, unrecoverable loss** of every title in that account (with the Feb-2026 CSAM-FP ban wave, a realistic event).
- Proposed change:
  - Push each chunk to its primary account AND to ≥1 **backup account**, each via a Pixel signed into that account (so every replica is free-unlimited original quality — NOT a saved-share copy).
  - Extend `split_info` per chunk: `replicas: [{account: "movies", device: "<serial>", remote_dir: "..."}, {account: "backup1", device: "<serial>", remote_dir: "..."}]` (or an entry-level `accounts` list). Backwards-compatible: absent `replicas` = today's single-account behavior.
  - `cmd_push` gains an account/replica target set (config-driven, IMP-A5); reuses the IMP-E7 multi-device machinery (one device per account). Honors the existing per-account content routing for the PRIMARY; backups are full mirrors.
  - Fetch/restore (X4) tries the primary account's Chrome profile first, falls back to a backup profile if a chunk is missing/the account is gone.
- Rationale: converts the single-point-of-failure into N-way redundancy on the free-unlimited path. Directly answers "add a backup account / make it super robust." Survives any single (or, with 2 backups, any double) account loss.
- Goal: every archived chunk exists in ≥2 accounts; losing any one account loses zero titles.
- Effort estimate: large
- Risk: high **(change-gate adjacent)** — extends the push upload loop, the `split_info` schema, and fetch-side routing; O-1 resume + journal scoping must hold PER REPLICA (a backup-lane failure must leave the primary's progress resumable). Plan against the change-gate checklist. Doubles (or triples) upload time + phone-storage churn — pairs with IMP-E5 phone cleanup and IMP-E7 parallel lanes.
- If skipped: the vault stays one CSAM-AI false positive away from losing a third of the library with no recovery path. Scenario: an innocuous anime frame trips the classifier, the anime account is disabled overnight, and all 140 anime titles (dummies locally, sole copy in that account) are gone permanently.
- Status: pending

## IMP-X2: Backup topology design + account-loss runbook (incl. the sharing decision)

- Category: security / documentation
- Priority: high
- Files: `docs/` (new `CLOUD_TOPOLOGY.md`); doctor checks (IMP-C3); config (IMP-A5)
- Current behavior: the account/device topology is implicit (3 content accounts, N devices, mapping in `DEVICE_ALIASES` for 2 of them); there is no written failover plan and no recorded decision on sharing-vs-replication.
- Proposed change:
  - Document the target topology: 3 content accounts + ≥1 dedicated **backup account**, each backup with a Pixel signed in; which account holds the primary vs replicas of what; how `DEVICE_ALIASES`/config encode it. (Resolves the open 4-Pixel topology question — now answered: **3 accounts**, movies/series/anime, multiple devices.)
  - Record the **sharing-vs-replication decision** (the §0 table) so no future session re-litigates it: replication is primary; Google Photos partner-sharing/saved-copy is a documented fallback ONLY for a specific case (e.g., a one-time bulk hand-off) with its quota + free-unlimited caveats spelled out.
  - **Account-loss runbook**: step-by-step recovery when an account is banned — identify affected entries (those whose `replicas` include the dead account), confirm a surviving replica, re-replicate to a fresh backup account, update `split_info`. Pairs with X4's automation.
  - Doctor checks: each configured account's Chrome session alive; each backup device reachable; replica counts per entry meet the policy (≥2).
- Rationale: redundancy is only as good as the operator's ability to use it under pressure; the runbook turns "account banned" from a panic into a checklist.
- Goal: a written topology + a tested recovery runbook; doctor flags any entry below the replica-count policy.
- Effort estimate: small-medium (mostly design/docs + doctor checks)
- Risk: low — docs + read-only health checks.
- If skipped: even with X1 replicas in place, a real ban becomes an improvised scramble; and the sharing-vs-replication question keeps getting re-asked (this very session answered it — capture it).
- Status: pending

## IMP-X3: Encrypted + obfuscated upload (anti-scanning / anti-copyright-match)

- Category: security
- Priority: medium
- Files: new `mvcrypt.py`; `cmd_push` (pre-upload wrap), `cmd_restore` (post-download unwrap); supersedes the privacy half of [[IMP-F1]]
- Current behavior: chunks upload as plain video files. Google's pipeline can hash-match them against copyright databases and run CSAM-AI on the frames — either can silently ban the account (the actual content is visible to the scanners). This is the root risk X1/X2 mitigate by redundancy; X3 attacks it at the source by making the content unscannable.
- Proposed change:
  1. **Encrypt** each chunk with AES (per-entry random key; keys stored LOCAL-ONLY under `~/.mediavault/keys/`, backed up to a password manager — NEVER uploaded).
  2. **Wrap** the ciphertext as a **Matroska attachment** inside a minimal valid `.mkv` whose video track is a black-frame dummy (same recipe family as `make_video_dummy`). Google sees "a black video"; the real payload is an opaque attachment the scanners don't decode.
  3. **Upload at Original Quality** (already the mode) → bytes preserved exactly → the attachment round-trips byte-exact (verified by the existing SHA256/deterministic-hash machinery, which already proves original-quality is lossless).
  4. **Restore**: download → `mkvextract attachments` → AES-decrypt → continue with the normal merge/verify path.
  - **MANDATORY feasibility spike FIRST** (the container constraint, Tier F header): confirm the Pixel Photos app (a) *ingests/auto-uploads* a 2-second-video / multi-GB-attachment MKV as a "video" at all, and (b) round-trips the attachment bytes EXACTLY at original quality. If the uploader rejects small-video/huge-attachment files or strips attachments, X3 is **blocked on this backend** → fall back to a real object store (IMP-F9, where arbitrary encrypted bytes upload natively and this whole wrapping dance is unnecessary).
  - Backwards-compatible: opt-in per entry (`encrypted: true`); existing plain chunks stay readable.
- Rationale: encryption defeats copyright hash-matching outright; the black-frame cover defeats the CSAM-AI classifier (nothing real for it to misclassify — directly mitigating the Feb-2026 false-positive ban risk that motivates this whole tier). Belt-and-suspenders with X1 redundancy: X1 survives a ban, X3 prevents it.
- Goal: opt-in encrypted entries that upload, round-trip byte-exact, restore correctly, and present Google's scanners with nothing matchable.
- Effort estimate: large (+ the spike)
- Risk: high **(change-gate)** — touches split/push/restore AND the integrity model (hashes now cover ciphertext/attachment; verify-or-bless needs an encrypted-variant story), and introduces a NEW catastrophic failure mode the system never had: **key loss = permanent unrecoverable data loss**. Requires a change-gate decision before implementation. The spike is low-risk (a few test uploads on a throwaway album); productization is the high-risk part.
- If skipped: the content stays scannable — every uploaded chunk is exposed to both copyright hash-matching and the CSAM-AI false-positive lottery; X1 redundancy limits the blast radius but doesn't prevent the ban that triggers it.
- Status: pending

## IMP-X4: Cross-account restore & self-healing replica repair

- Category: robustness
- Priority: medium
- Files: `mainfetch.py` (multi-profile fetch fallback), new `cmd_repair_replicas`; depends on X1's `replicas` schema
- Current behavior: fetch uses exactly one Chrome profile per content category. If a chunk is missing from the primary account (or the account is gone), the fetch simply fails — there is no fallback to a backup account, and no mechanism to rebuild a lost replica.
- Proposed change:
  - Fetch tries the primary account's profile first; on a per-chunk miss, automatically retries against each backup account's profile (the `replicas` list says where copies live).
  - `cmd_repair_replicas [--id|--all]`: audit every entry against the replica-count policy; for any entry below policy (e.g., a replica lost to a banned account), restore from a surviving replica and re-push to a fresh backup account, updating `split_info`. The X2 runbook, automated.
  - Daemon hook (Tier S): when X5's canary detects a dead account, queue `cmd_repair_replicas` for all affected entries.
- Rationale: redundancy that can't be *used* automatically (fetch fallback) or *rebuilt* automatically (replica repair) is half a solution; this closes the loop so a single account loss self-heals.
- Goal: a fetch succeeds as long as ANY replica survives; after an account loss, one command (or the daemon) rebuilds full redundancy.
- Effort estimate: medium-large
- Risk: medium — fetch-side fallback is additive (more profiles tried); the repair command re-pushes (reuses journaled `cmd_push`), so no new rollback contract — state that in the PR.
- If skipped: X1 stores replicas but nothing uses them automatically — a banned account still means manual, error-prone recovery (find affected entries, hand-fetch from a backup, re-push), exactly the scramble X2's runbook tries to avoid.
- Status: pending

## IMP-X5: Account-health canary & ban early-warning

- Category: robustness
- Priority: medium
- Files: doctor (IMP-C3) extension; daemon scheduled check (Tier S); per-account sentinel item
- Current behavior: an account ban is discovered only when a fetch fails — potentially weeks after the ban, long after the window to react.
- Proposed change:
  - Maintain a tiny **sentinel item** per account (a known dummy chunk). A scheduled check (doctor / daemon) confirms each account's Chrome session is alive AND the sentinel is still searchable in that account's Photos. A missing sentinel or a login-wall = early ban/expiry signal.
  - Surface as a doctor FAIL + (via the Tier S daemon) an in-client "⚠️ Vault account needs attention" alert, so the user reacts within hours, not weeks — while surviving replicas (X1) still allow a clean re-home.
  - Distinguish session-expiry (re-login, IMP-C6) from an actual ban (sentinel gone) where possible.
- Rationale: turns a silent, weeks-late discovery into an early warning, maximizing the time to recover from replicas before any second account is also at risk.
- Goal: an account ban/expiry is flagged within one check cycle, with a clear action (re-login vs re-home-from-replica).
- Effort estimate: medium
- Risk: low — read-only health probing (one search per account per cycle); main cost is keeping the Selenium session-check cheap and not itself tripping rate limits.
- If skipped: bans are found late (at next fetch), shrinking the recovery window — and if two accounts drift toward bans simultaneously, the late discovery could catch both before any re-home happens.
- Status: pending

---

## Sources (2026 research)

- Shared-album save → independent copy survives owner deletion: [Google Photos Community — "Adding shared photos to library, what happens if the original owner deletes or unshares?"](https://support.google.com/photos/thread/151284?hl=en) · [picbackman guide](https://www.picbackman.com/tips-tricks/how-to-save-photos-from-shared-albums-to-photos-library/)
- Owner-account deletion removes the shared album for everyone: [Google Photos Community — "when owner … deletes or deactivates his account …"](https://support.google.com/photos/thread/930514) · [Overdrive — what happens to shared files when you delete your Google account](https://www.overdrive.tools/blog/organize/what-happens-shared-files-delete-google-account)
- Partner-sharing quota behavior (free while shared, counts the moment sharing stops): [Google Photos Community — "Does partner sharing count against storage?"](https://support.google.com/photos/thread/83425398/does-partner-sharing-count-against-storage?hl=en) · [Metadata Fixer — Partner Sharing guide](https://metadatafixer.com/learn/google-photos-partner-sharing-guide)
- Original Quality preserves bytes; Storage Saver re-encodes (~90% shrink): [Google Photos backup-quality help](https://support.google.com/photos/answer/6220791) · [WinXDVD — does Google Photos compress video](https://www.winxdvd.com/resize-video/google-photos-compress-video.htm)
- Matroska attachments carry arbitrary binary data inside a valid MKV: [matroska.org — Attachments](https://www.matroska.org/technical/attachments.html) · [IETF Matroska spec](https://www.ietf.org/archive/id/draft-ietf-cellar-matroska-05.html)
- Content scanning + the Feb-2026 CSAM-AI false-positive ban wave (instant, no recourse): [PiunikaWeb — Google Photos false CSAM flags](https://piunikaweb.com/2026/02/03/google-photos-false-csam-flags-users-locked-out/) · [Metadata Fixer — what happens when Google bans your account](https://metadatafixer.com/learn/google-account-banned-how-to-protect-photos)
