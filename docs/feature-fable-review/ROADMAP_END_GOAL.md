# MediaVault → Couch-Vault: The End-Goal Roadmap

**Written 2026-06-12 (fable-review session).** The phased path from today's PC-side CLI to the
target experience, stated by the user as:

> *"not even come to my computer — just open the app in apple tv or my projector (with ugoos am6b),
> use jellyfin or emby or infuse app, go through all the movies, series, anime — select there itself —
> have the option to fetch — or play — and it should fetch in the background part by part or fully and
> notify me when it is done and then I can play. Once I finished viewing, it also need to ask me
> directly and archive the file. All these given that my alienware setup keeps running."*

**Locked decisions** (SESSION_BRIEF.md): Jellyfin-first · in-client-only notify/archive ·
docs-first session (no code here) · 4× Pixel 1 XL upload path untouchable.
**Research grounding**: `RESEARCH_MEDIA_SERVERS.md`, `RESEARCH_STORAGE_STREAMING.md`,
`JELLYFIN_SETUP_GUIDE.md`. **Task tracking**: Tiers S/U (+ supporting A–R items).
**Predecessor doc**: `apple_tv_ui_roadmap.md` (2026-05) — superseded by this file where they
disagree (banner added there; its §5 dummy-detection design is stale, its Jellyfin-choice analysis
remains valid and confirmed).

---

## 0. The architecture in one picture

```
   COUCH                          ALIENWARE (always on)                      CLOUD
┌──────────────┐   Jellyfin   ┌──────────────────────────────┐   Selenium/  ┌─────────────┐
│ Apple TV     │   API/HTTPS  │  JELLYFIN (serves C:\Media)  │   browser    │ Google      │
│  Swiftfin    │◄────────────►│   ▲ webhooks │ API           │   session    │ Photos      │
│  Infuse      │              │   ▼          ▼               │◄────────────►│ (2 accts)   │
│ Ugoos AM6B+  │              │  MVDAEMON (Tier S)           │              │  unlimited  │
│  CoreELEC+   │              │   policy: request/notify/    │              │  originals  │
│  Jellyfin    │              │   grace-archive/prefetch     │     ADB      │             │
│  for Kodi    │              │   job queue → CLI verbs      │──(4× Pixel──►│             │
│ phones/web   │              │  MAIN.PY / MAINFETCH.PY      │   1 XL push) │             │
└──────────────┘              │   (untouched core + journal) │              └─────────────┘
                              └──────────────────────────────┘
   The user only ever touches the left column. The daemon is the only NEW component.
```

The core loop, mapped to the user's words:

| User's requirement | Mechanism | Task |
|---|---|---|
| "go through all the movies, series, anime" | Jellyfin libraries over `C:\Media`; dummies make archived titles browsable tiles; NFO+art make them pretty | S1, U3 |
| "select there itself — option to fetch — or play" | Local title → plays instantly. Archived title → playing its dummy IS the fetch request (webhook-observed) | S3 |
| "fetch in the background part by part or fully" | Daemon queues `fetch_restore`; chunks already fetch in parallel; "part by part" playback = S6 experiment | S2, S3, S6 |
| "notify me when it is done... then I can play" | `DisplayMessage` popup + tile flips playable + "Ready to Watch" home row | S3, U2 |
| "ask me directly and archive the file" | Grace-period auto-archive + in-client holds (Keep collection / replay / action stubs) | S4 |
| "alienware keeps running" | Daemon as Windows service; doctor-on-schedule; logs | S2, C3, A3 |

## 1. Phases

### Phase 0 — Foundation on glass (1-2 evenings, zero code)
Execute `JELLYFIN_SETUP_GUIDE.md`: server, 3 libraries, NVENC, plugins, Swiftfin+Infuse on Apple TV,
Jellyfin-for-Kodi (add-on mode) on the Ugoos. Run the §10 validation checklist; fill the
**client capability matrix** (which clients render DisplayMessage; what dummy-play looks like on each).
→ **IMP-S1.** Exit: you can browse the whole vault from the couch; archived items show as
2-second tiles; everything local plays.
*Worth doing THIS WEEK — it delivers visible value standalone.*

### Phase 1 — Critical repairs before automation (short PRs)
The review found bugs that automation would amplify; fix before the daemon multiplies invocation rates:
**IMP-C12** (scan_unprepped/local_status alias crashes — broken TODAY), **C13** (single-id alias
handling), **C14** (parser hang papercuts), **A10** (requirements.txt), and decide the gated
**R6** (merge-failure dummy loss — directly load-bearing for the couch catalog) + **R7** (journal
clobber). Optionally **A12** (CI) to lock the suite in.
Exit: `pytest -q` green in CI; the daily-driver commands crash-free on alias-bearing data.

### Phase 2 — The daemon heartbeat (the big one)
**IMP-S2** (mvdaemon service: webhook listener, path→id, job queue over CLI verbs, Jellyfin client,
status endpoint) + **IMP-S3** (fetch-request/notify flow) with **IMP-E9**'s refresh client and
**IMP-C6** (session-expiry detection — the daemon's most likely silent killer).
Exit criterion = the headline demo: *pick an archived movie on the Apple TV, play its dummy, put the
remote down; later a popup says it's ready; press play and watch it.*

### Phase 3 — Close the loop (archive + binge)
**IMP-S4** (grace-period archive flow + Keep controls) + **IMP-U1** (enrichment-before-archive:
trickplay/chapters/intro-fingerprints generated post-restore, BEFORE re-archive — permanent polish)
+ **IMP-S5** (smart prefetch: watching N fetches N+1, archives N−2) + **IMP-E4** (durable watch-state)
+ **IMP-U2** (status home rows). Supporting: **E5** (phone auto-cleanup — keeps the upload pipeline
self-feeding; gated on its high-risk checklist).
Exit: a 13-episode archived season binge with ONE wait (episode 1); disk usage self-regulates;
home screen shows Fetching/Ready/Leaving-soon rows.

### Phase 4 — Beautiful library
**IMP-E3** (TMDB/TVDB/AniDB enrichment) + **IMP-U3** (NFO + artwork emission + backfill of ~570
entries) + **IMP-U4** (DV-FEL/Apple-TV playback-path guide + per-content hints). Optionally
**E1** (subtitle pre-extraction) into the same prep/enrichment pipeline.
Exit: every tile — dummy or real — has the right title, poster, synopsis; anime ordering correct;
reference content verifiably plays at reference quality on the projector.

### Phase 5 — Feels-instant experiments (spikes with written verdicts)
**IMP-G2** (gphotosdl spike) feeding **IMP-S7** (fetch-engine hardening — do this regardless of
verdict direction) · **IMP-S6** (watch-while-fetching T2: chunk-1-early) · then **IMP-S8**
(T3 proxy-streaming) only if S6's verdict says more latency cutting is needed.
Exit: fetch is boring-reliable under daemon load; time-to-first-frame measured and either
accepted or reduced; the "can I stream on the fly?" question closed with evidence.

### Phase 6 — Polish & resilience (ongoing)
**IMP-U5** (the C# Jellyfin plugin — runtime overrides, badges, explicit Restore UI) ·
**IMP-E12/F10** (the ops web dashboard on the daemon) · **IMP-F3-replication** (second-account chunk
replication — cheap survival against account loss) · **IMP-F9** (multi-cloud abstraction — the
strategic hedge; revisit yearly or on any Google policy signal) · **IMP-F5** (library-JSON git backup
— arguably belongs in Phase 1 for its backup half) · core-CLI quality train continues
(A2 argparse → A4 --json → A5 config → A3 logging — each makes the daemon integration cleaner;
none block it).

## 2. Dependency weave (what actually blocks what)

```
S1 ──► S2 ──► S3 ──► S4 ──► S5
        │      │      └─ U1 (enrichment gate inside S4's flow)
        │      └─ C6 strongly advised; E9 client built here
        ├─ A3/A4/A5 make S2 CLEANER but do NOT block it (subprocess + print contract works)
        └─ C12/C13 BEFORE daemon iterates the library at scale
E3 ──► U3 (NFO needs the metadata) ; U4 independent
G2 ──► S7 ; S6 independent of S7 ; S8 after S6 verdict
R6 decision BEFORE S3 ships (dummy loss would hit daemon-triggered restores)
E5 before S5 runs long unattended stretches (phones fill otherwise)
```

## 3. Emby / Plex — what taking each would add (the user asked)

| | Keep as | What it adds if adopted | What it costs | Verdict |
|---|---|---|---|---|
| **Jellyfin** | PRIMARY | Free webhooks/API/plugins; the whole Tier S design; fastest-moving ecosystem | Self-managed; occasional plugin churn | Build here ✅ |
| **Emby** (lifetime owned) | Warm fallback | Slightly slicker stock apps; Emby-for-Kodi NG is excellent; hardware transcode incl.; same C# plugin skills | Webhooks are Premiere-gated (owned, so moot); closed core = harder debugging; smaller community; porting tax ~weeks if ever needed | Install once during Phase 0 for A/B familiarity; don't automate against it ⚠️ |
| **Plex** ($749 lifetime) | Skip | Most polished first-party clients; best zero-config remote streaming; Plexamp | NO plugin/virtual-item surface → the request/notify/archive flow is unbuildable; price tripled (the remembered "buy before increase" window already passed — it WAS the increase) | Do not buy for this project ❌ (reconsider only for non-vault needs like remote family sharing) |

Infuse (already owned) gives the "Plex-grade polish" on Apple TV against Jellyfin anyway.

## 4. "Can I do streaming playback on the fly?" — the answer (evidence in RESEARCH_STORAGE_STREAMING §2)

- **Now (T0/T1):** No true instant-stream — but couch-triggered background fetch + in-client notify
  (Phase 2) removes every PC visit, and **smart prefetch (Phase 3) makes episodic watching feel
  instant from episode 2 onward**.
- **Near (T2, Phase 5):** Watch chunk 1 while the rest downloads — mkvmerge split parts are already
  valid playable files; ~10-15 min to first frame for a 70 GB remux.
- **Far (T3, spike-gated):** gphotosdl-style local proxy streaming the Google original as it
  downloads — minutes-to-seconds start, single-file titles, fragile, Google-coupled.
- **Never (T4, under current policy):** direct TV↔Google streaming — no API for non-app-uploaded
  originals (verified post-2025-03-31 lockdown).

## 5. Risks the roadmap explicitly carries

1. **Google platform risk** (web-session automation + Pixel grandfather) — mitigations: S7 hardening,
   undetected-chromedriver lever, F3-replication, F9 hedge, quota telemetry (E6). Detailed register:
   RESEARCH_STORAGE_STREAMING §1.3 + BLOCKERS_AND_MOONSHOTS.md.
2. **Destructive automation** (S4 auto-replace, E5 phone deletion) — every such loop ships with
   grace periods, Keep overrides, caps, dry-run-first, and the CLI's own journal/PONR guards
   underneath; rollback change-gate decisions (R6/R7) are made BEFORE these phases, not during.
3. **Client heterogeneity** (DisplayMessage rendering varies) — measured in Phase 0 BEFORE designs
   freeze; action-stub pattern is the universal fallback.
4. **Scope creep** — the daemon REUSES CLI verbs; any temptation to reimplement pipeline logic inside
   the daemon is a design smell and a change-gate red flag.

## 6. Topology — ANSWERED (2026-06-12) + the redundancy it demands

The account topology is **3 Google accounts — movies, series, anime** — with multiple Pixel devices.
This made two things concrete and added a phase-spanning workstream:

- **Correctness fix:** anime now being its own account means the 2-profile fetch routing is wrong for
  `ani-*` → **IMP-C16** (Band 0/1; do early — the first archived-anime restore fails without it).
- **Resilience (new Tier X), folded into Phase 3/6:** 3 accounts = a single point of failure, made
  urgent by the Feb-2026 CSAM-AI false-positive ban wave (instant, unrecoverable). The plan:
  **IMP-X1** multi-account chunk replication (every chunk in ≥2 accounts, all on the free-unlimited
  Pixel path — Google Photos *sharing* is NOT a safe backup, see `improvements_tierX.md` §0),
  **X2** topology + account-loss runbook, **X5** ban canary, **X4** cross-account self-heal,
  **X3** encrypted/anti-scanning upload (spike-gated). Sequence X1/X2/X5 alongside Phase 3 (they reuse
  E7 multi-device push + E5 phone cleanup); X3/X4 in Phase 5/6.

Everything in Phases 0-2 proceeds independently; X-work joins from Phase 3.
