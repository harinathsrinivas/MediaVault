# Tier S — Streaming & Media-Server Integration (the couch-vault backbone)

> **Added 2026-06-12 (fable-review session).** The tasks that turn MediaVault from a
> PC-side CLI into the engine behind a couch-only, Netflix-like flow: browse the vault on
> Apple TV / the Ugoos projector via Jellyfin, select → fetch in the background → get told
> in-client when it's ready → watch → get the archive handled automatically. Phasing,
> rationale, and the research behind every choice live in
> [`ROADMAP_END_GOAL.md`](ROADMAP_END_GOAL.md)
> (the master roadmap), `RESEARCH_MEDIA_SERVERS.md`, and `RESEARCH_STORAGE_STREAMING.md`.
>
> **Locked decisions shaping this tier (SESSION_BRIEF.md):** Jellyfin-first (Emby lifetime
> = fallback; Plex = do-not-buy); **in-client-only** notifications and archive prompts
> (no phone push / Telegram / separate dashboard for the core flow); the 4× Pixel 1 XL
> unlimited-upload path is untouchable.
>
> **Standing rules:** every task here REUSES the CLI verbs (`fetch_restore`, `replace`,
> `recover`) — the daemon orchestrates, it never reimplements; anything brushing rollback
> behavior goes through the change-gate; any new library iterator skips/resolves
> `multi_ep_alias` (IMP-C12 lesson).
>
> **Attribute key:** `Risk` = blast radius of MAKING the change. `If skipped` = what the
> end goal loses, with a scenario.

---

## IMP-S1: Phase-0 — Jellyfin stand-up & client validation matrix

- Category: integration (infrastructure, no MediaVault code)
- Priority: high
- Files: none in this repo (Alienware Jellyfin install); results recorded into `JELLYFIN_SETUP_GUIDE.md` §10 + a new `CLIENT_MATRIX.md`
- Current behavior: No media server is wired to the vault. Plex exists on the machine historically (Emby's ffmpeg is even MediaVault's `FFMPEG_PATH`), but nothing serves the `C:\Media` tree with vault-awareness.
- Proposed change: Execute `JELLYFIN_SETUP_GUIDE.md` end-to-end (install as service → 3 libraries with exact toggles → NVENC → plugins: Webhook, Intro Skipper, AniDB/AniList, Home Screen Sections → clients: Swiftfin + Infuse on Apple TV, Jellyfin-for-Kodi in add-on mode on the Ugoos/CoreELEC). Run the §10 validation checklist and **record the client capability matrix**: which clients render `DisplayMessage` popups (web/AndroidTV/iOS confirmed by research; Swiftfin/Infuse/Kodi UNKNOWN — this measurement decides S3's notify design), direct-play behavior per client, dummy-play behavior (what exactly happens when each client "plays" a 10 KB dummy).
- Rationale: Every subsequent S-task builds on a measured, working Jellyfin baseline — especially the DisplayMessage matrix, which is the difference between "popup notify" and "action-stub fallback" designs.
- Goal: All §10 checklist boxes ticked; CLIENT_MATRIX.md filled for ≥4 clients; library counts match the JSONs.
- Effort estimate: medium (an afternoon + an evening of TV-side testing)
- Risk: low — zero MediaVault code; Jellyfin only READS `C:\Media` (deletion disabled per guide §3.4/§4).
- If skipped: S2-S5 get built against guesses; the first "notify doesn't show on Infuse" discovery happens after the daemon ships instead of before it's designed.
- Status: pending

## IMP-S2: `mvdaemon` — the always-on vault service

- Category: integration (new component)
- Priority: high
- Files: new `mvdaemon.py` (FastAPI app + job queue + Jellyfin client + webhook listener); service install via NSSM or Task Scheduler; config keys (IMP-A5 or interim constants)
- Current behavior: Every operation is a foreground CLI invocation on the PC. Nothing can react to TV-side events; "fetch from the couch" has no listener.
- Proposed change: One long-running Windows service hosting:
  1. **Webhook listener** (`POST /jf-events`) consuming the Jellyfin Webhook plugin (PlaybackStart/Stop/progress, item added, sessions) with JellyHookDebouncer-style noise filtering.
  2. **Path→id resolver**: webhook payloads carry file paths; map via library `folder_path+filename` (build the index at startup, refresh on library mtime change; share logic with IMP-E11).
  3. **Job queue + workers**: serialized-per-entry, parallel-across-accounts execution of EXISTING CLI verbs (`fetch_restore`, `replace`, `restore`, `cleanup_phone`…) as subprocesses (today's print/return contract; `--json` when IMP-A4 lands). *arr-style states: `requested → fetching → restoring → ready → watched → grace → archived`, persisted to a small `~/.mediavault/daemon_state.json` so a daemon restart resumes cleanly (crash mid-job is already covered by the CLI's own rollback/resume contracts — the daemon NEVER adds its own cleanup logic; change-gate untouched).
  4. **Jellyfin API client**: targeted library refresh, Sessions list + DisplayMessage, collection management, played-state queries.
  5. **Ops surface**: `GET /status` (queue, jobs, last errors) — the seed of IMP-E12's web UI; doctor (IMP-C3) on startup + schedule once it exists.
- Rationale: The single missing component between "CLI on a PC" and the entire end goal; every flow in S3-S5 is a policy loop inside this service.
- Goal: Service survives reboots, ingests webhooks from a real TV session, runs a fetch_restore end-to-end triggered by an HTTP call, and exposes status.
- Effort estimate: large
- Risk: medium — new component, but strictly a CALLER of existing commands (no core-path edits). Real risks: concurrent daemon jobs vs manual CLI invocations on the same entry (mitigate with a per-entry lockfile honored by the daemon; manual CLI keeps priority), and Selenium fetch jobs needing an interactive-ish session (run the service as the logged-in user, not LocalSystem — same constraint mainfetch already has).
- If skipped: the end goal simply does not exist — every couch action keeps requiring a walk to the PC.
- Status: pending
- Note (IMP-E14 cross-ref, 2026-06-23): the serialized web worker (`webui/server.py`) is the seed of this daemon and now performs `fetch_restore` end-to-end with live chunk-% progress (IMP-E14 Phase 2, `feature/imp_e14_fetch_in_ui`).

## IMP-S3: In-client fetch request + "ready" notify flow

- Category: integration (daemon policy)
- Priority: high
- Files: `mvdaemon.py` policy module; no main.py changes
- Current behavior: Selecting an archived title on the TV plays a 2-second black dummy and nothing else happens.
- Proposed change: The S2 daemon interprets **PlaybackStart on a dummy-state entry as the fetch request**:
  1. Debounce (a scroll-by autoplay-preview must not trigger a 70 GB fetch — require N seconds of playback or a completed dummy play; tune on real client data from S1).
  2. Queue `fetch_restore <id>` (episodes-range for season selections); reply immediately via `DisplayMessage` to the requesting session ("⏳ Fetching Inception — ~45 min, I'll tell you here") or, where that client renders nothing (S1 matrix), update the item's overview text + a "⏳ Fetching now" collection.
  3. On completion: targeted library refresh (tile flips playable), `DisplayMessage` "✅ Ready to play", add to "Ready to Watch" collection (Home row via U3).
  4. On failure: surface the CLI's structured outcome (resume hint / RollbackHardFail resume_cmd) as a "⚠️ Vault needs attention: <reason>" message + a `needs-attention` collection entry; never retry destructive steps automatically.
  - Optional polish (decide during S1): regenerate request-dummies as 10-second "FETCHING — request registered" title cards so even notification-blind clients show state in the player itself.
- Rationale: This IS the "select there itself → have the option to fetch → notify me when done" requirement, with zero client-side code.
- Goal: From Swiftfin/Infuse/Kodi: select archived title → play dummy → within ~10 s an in-client acknowledgment exists → later, in-client ready-notify + the title plays for real.
- Effort estimate: medium (on top of S2)
- Risk: low-medium — policy code in the daemon only; the worst bug class is unwanted fetch triggers (debounce + a daily fetch-count guard + easy cancel via stopping within grace seconds).
- If skipped: browsing works but requesting doesn't — the user can SEE the vault from the couch yet still has to walk to the PC to type `fetch_restore`, which is the exact pain being abolished.
- Status: pending

## IMP-S4: Post-watch archive flow (grace-period + in-client controls)

- Category: integration (daemon policy)
- Priority: high
- Files: `mvdaemon.py` policy module; uses existing `replace` CLI verb; Keep/grace state in daemon_state or entry watch_state (IMP-E4)
- Current behavior: After watching a restored file, it sits on local disk forever until the user manually runs `replace`.
- Proposed change: On webhook PlaybackStop with played-to-completion (or ≥90% progress, debounced):
  1. Enter **grace period** (default 48 h, config): `DisplayMessage` "🗄️ Watched — will re-archive in 48 h. Play again or add to 'Keep' to hold it."
  2. In-client controls (all native client gestures, per the in-client-only decision): adding the item to the **"Keep" collection** blocks archiving; replaying resets the clock; optional **action-stub items** ("🗄️ Archive now" / "📌 Keep") in a "MediaVault Actions" library for explicit button-style control on every client.
  3. On grace expiry: verify nothing is playing it, run `replace <id>` (upload still valid — the entry was already archived once; `uploaded` stays true), targeted refresh, `DisplayMessage` "Archived — disk freed: 68 GB".
  4. Safety rails: never auto-archive `local_ready` (never-uploaded) items; never act while ANY session is playing the file; daily auto-archive cap; everything logged + visible in the status UI.
- Rationale: This is the "once I finished viewing, ask me directly and archive" requirement — translated to in-client primitives (the literal modal-with-buttons does not exist across TV clients; grace-plus-Keep is the faithful equivalent, with action-stubs as the explicit-button variant).
- Goal: A watched movie re-archives itself within the grace window unless held, with the user informed in-client at each step; disk usage trends back down without any PC visit.
- Effort estimate: medium (on top of S2)
- Risk: medium — invokes `replace` (a destructive-by-design command) automatically. Contained because replace's own guards (uploaded-only, PONR semantics, journal) all still apply; the daemon adds conservative policy on top. The rails list above is mandatory scope, not optional.
- If skipped: the archive half of the loop stays manual; after a month of couch-driven fetching, the local disk is full of watched files and someone has to bulk-replace at the PC anyway.
- Status: pending

## IMP-S5: Smart prefetch + binge policy (the Netflix "smart downloads" translation)

- Category: integration (daemon policy)
- Priority: medium-high
- Files: `mvdaemon.py` policy module; depends on S3/S4 + watch-state (IMP-E4)
- Current behavior: Each fetch is reactive — selecting episode N+1 of an archived season starts a fresh wait.
- Proposed change: Episodic policy loop: when episode N of a season starts/finishes playing, ensure N+1 (and optionally N+2) are queued for fetch; auto-archive N−2 after its grace (S4). Pre-stage the next season's first episode at season finale. Movie-side: optional "tonight's queue" — items added to a "Watch Soon" collection get prefetched off-peak. Config: lookahead depth, off-peak window, per-account parallelism (respects the mov/tv account routing; topology answer from REVIEW_NOTES §E1 sets lane count).
- Rationale: The research's highest-leverage finding (RESEARCH_STORAGE_STREAMING §4): sequential bingeing with lookahead-1 makes fetch latency INVISIBLE from episode 2 onward — most of the "instant streaming" feel at none of the T2/T3 complexity.
- Goal: Binge a 13-episode archived season with a wait on episode 1 ONLY; local footprint stays ≤ lookahead+grace window.
- Effort estimate: medium
- Risk: low-medium — pure scheduling policy over S3/S4 primitives; failure mode is wasted prefetches (bounded by config caps).
- If skipped: every episode transition reintroduces the fetch wait — the difference between "feels like Netflix" and "feels like a download manager".
- Status: pending

## IMP-S6: Watch-while-fetching experiment (T2 — chunk-1-early)

- Category: experiment / spike
- Priority: medium
- Files: spike branch; possibly small `mainfetch` ordering tweak (chunk-priority) + daemon serving logic; verdict doc in `../docs/feature-fable-review/`
- Current behavior: A split title is playable only after ALL chunks arrive + merge completes (restore is all-or-nothing).
- Proposed change: Validate and productize the T2 tier from `RESEARCH_STORAGE_STREAMING.md` §2: mkvmerge-split chunks are independently playable Matroska files — so fetch chunks strictly in order, expose chunk 1 as soon as it lands+verifies (as `<Title> — Part 1` via a temp library item, or a Kodi playlist), let the user start watching while 2..N download; the normal verified merge runs afterward and the parts presentation disappears. Spike questions: client behavior at part boundaries (manual next vs playlist autoplay), whether a growing pre-merge file is viable on any client, interaction with trickplay/progress tracking, and UX acceptability on the projector vs Apple TV. WRITTEN VERDICT required (proceed / park) before any productization.
- Rationale: Cuts time-to-first-frame for big movies from ~full-fetch to ~one-chunk-fetch (≈10-15 min for 9.6 GB chunks on a fast line) — the biggest remaining latency win after S5, without touching Google-side mechanics.
- Goal: A measured demo: 70 GB archived movie playing within ~1 chunk-time of selection, with documented client caveats and a go/no-go.
- Effort estimate: medium (spike) → large (productize)
- Risk: medium — fetch-ordering change is trivial, but anything presented to Jellyfin mid-restore must stay strictly OUTSIDE the restore pipeline's contract (read-only on `restore/`; the merge/verify/PONR sequence untouched — change-gate review for anything closer than that).
- If skipped: movie-night spontaneity keeps paying the full-fetch wait (S5 can't help non-episodic picks); acceptable if T1+S5 prove "good enough" in practice — that's what the verdict decides.
- Status: pending

## IMP-S7: Fetch engine hardening (gphotosdl/CDP adoption — execute the IMP-G2 verdict)

- Category: robustness / refactor
- Priority: medium-high
- Files: `mainfetch.py` (or its replacement shim); follows the IMP-G2 spike's written decision
- Current behavior: Keystroke-simulation Selenium against undocumented Google Photos DOM; selector drift = total fetch outage; no session-expiry detection (IMP-C6); folder-polling harvester.
- Proposed change: Whichever way G2 decides: (a) gphotosdl-shim — mainfetch becomes id-resolution + HTTP GETs against the local proxy, keeping hash-routing; or (b) cherry-pick — CDP download events replace folder polling, gphotosdl's session/throttle/re-auth patterns layer onto today's flow, `undetected-chromedriver` (already in requirements) as the bot-detection lever. Either path: C5 (real fallback query) + C6 (session expiry) land as part of this, two Chrome-profile lanes become daemon-schedulable in parallel.
- Rationale: The daemon (S2) makes fetch a nightly unattended operation — the most fragile component must become the most boring one before it runs while everyone sleeps.
- Goal: A month of daemon-driven fetches with zero selector-related failures; session expiry surfaces as a doctor FAIL + in-client alert within minutes, not as a silent 90-minute timeout.
- Effort estimate: large
- Risk: medium-high — replaces/refactors the fetch engine; the hash-routing integrity layer and two-profile routing are the invariants every candidate must prove before cutover (run old+new in parallel on a test set first).
- If skipped: the end-goal flow inherits mainfetch's fragility at 10× the invocation rate; statistically, the first silent Google UI change lands within months and takes the whole couch experience down with it.
- Status: pending

## IMP-S8: T3 proxy-streaming spike (play originals straight through a local proxy)

- Category: experiment / moonshot
- Priority: low (explicitly AFTER S6's verdict)
- Files: spike only; verdict doc
- Current behavior: All playback requires bytes fully fetched to local disk first (modulo S6's part-1 trick).
- Proposed change: Probe the T3 tier (`RESEARCH_STORAGE_STREAMING.md` §2): a gphotosdl-style local HTTP proxy that triggers the Google-Photos original download and streams bytes through as they arrive; a `.strm`-to-localhost (or direct-URL) Jellyfin item plays it with seconds-to-minutes of buffer instead of a full fetch. Spike questions: range-request behavior on the GP download stream (seek!), single-file titles only vs chunk stitching, client tolerance (Infuse's .strm bug #12306 — prefer the proxy-URL form), integrity posture (bytes bypass verify-or-bless until a parallel background fetch completes — define the trust story), and Google-side fragility/ToS exposure. WRITTEN VERDICT; productize only if S6 proved insufficient AND the seek story works.
- Rationale: The only path to true press-play-now streaming for spontaneous movie picks; also the riskiest and most Google-coupled — hence spike-last.
- Goal: A documented demo or a documented dead-end; either outcome closes the "can I stream on the fly?" question with evidence (current answer: tiered, see research doc).
- Effort estimate: medium (spike)
- Risk: low as a spike (isolated proxy + a test .strm item); HIGH as a product (Google dependency, no integrity guarantee mid-stream) — which is exactly what the verdict weighs.
- If skipped: "instant play" ceiling stays at S6's chunk-1 latency; the 1%-possible tracker (BLOCKERS_AND_MOONSHOTS.md) keeps it on record for the day Google's constraints shift.
- Status: pending
