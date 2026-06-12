# Fable Review Session — Brief

**Session start:** 2026-06-12
**Branch:** `feature_fable_review`
**Model:** Claude Fable 5 (max effort)
**Scope decision:** Docs + roadmap only — no feature code this session.

This file preserves the complete, verbatim originating prompt (required later for
the PR body per `docs/git-pr-conventions.md`) and the user's answers to the
session-setup questions. Read `STATUS.md` in this folder first for current
progress and the resume protocol.

---

## Original task prompt (verbatim)

> Create a feature_fable_review branch do all your changes in that branch for below.
> From the main branch,  go through this entire project again, go throug each project file (except legacy and unused ones) go through the current architecture. Do ultra think and deep analysis for this whole task.
> Check if there are any changes needed for the architecture, do the same to architecture, readme and any other files needed. Also create a graph version of the architecture similar to what graphiphy plugin creates.
>
> recent pull requests - what was changed in the recent pull requests.  especially the auto roll back feature and the fixing of hashing part which was both a big change. Now also go through all the improvement tiers, and each task in them.
> Do a through review of the pending ones- check if any other improvements needs to be added. Improvement can be anything from a code bug, to logging addition to a ambitious feature we can add - like a complete modern UI to track all our operations in Web UI for example.
> Also for each of the imp tasks , also add few more attributes, risk - to indicate how big the impact of the change will be (if it will modify most of the working commands now), issues_if_not_implemented (or name it better) - to indicate what is the failure or issue that will happen if this fix is not done. You can also add an example or scenario in this field.
> create more tiers of imprevements if needed , log all in similar format.
> Also consider my end goal, I want to have a seamless - netflix like streaming service - similar to apple tv UI. Also note that I already have paid versions of emby (lifetime), jellyfin (not yet but thinking of using because of open source and configurability) and also if needed I can also purchase plex life time key before the price increases next month.  But with all these, or maybe only one upto you - how to take my project to the next level?
> Can I do streaming playback on the fly? what other ways I can do to enrich my experience? My final end goal - as I mentioned - I need to be able to not even come to my computer - just open the app in apple tv or my projector (with ugoos am6b) just use jellyfin or emby or infuse app - go through all the movies , series , anime -- select there itself - have the option to fetch -- or playy -- and it should fetch in the background part by part or fully and notify me when it is done and then I can play. Once I finished viewing , it also need to ask me directly and archive the file. All these given that my alienware setup keep running where this program will be running.
> Just create all imprevements needed for this end goal - it can be a step by step process or phase by phase - but give me proper way to reach there. By having some genuinely usefull features or cool optimizations or improvements. Even if it needs to re structure my entire storage structure or methods (But make sure you use the unlimited uploads which now I do using 4 different pixel 1 XL files in parallel). Also again go through all the projects available online related to our project and fetch the best features from them. Also see if any netflix or other streaming site features we can steal or use in our project or if any opensource alternatives which will help our projects.
> Document everything neatly in .md files, and also have a proper master .md file to indicate what each of these files mean and contain. Use whatever tool you want to use and access any program in this system or web you want to use. Install any other new softwares if you want to if you think that will help you. I give you full free hand. But document everything in such a way that this session is never needed again. Any agent or new session of claude or future sessions should be clearly understand the bigger picture and each tasks in a detailed way. For each imp tasks, if you think any other attributes are needed, add that also. if you genuinely think something is a hard blocker or can never be done - note down that also, later I can check alternatives for those items as well.
> Do super deep analysis and web scrap or any web search needed for this task  no limits.
> but keep track of your entire task also - so that if limits are reached you should just be able to resume from where you left off from my next prompt (after limits reset) - you are free to use any approach - saving status regularly , or keeping the memory in a file or any way you think is best.
> Ask me if any questions or decisions or ambiguity now itself before starting the research. Also ask if in between if any doubts or questions in the middle even when you do the research. Then based on my response you continue your deep research. You need to be super intelligent and do a PHD level research analysis. Even if you think some approach is never tried earlier or before and you think that it is possible even 1% let me know and track it , we can plan and make it work in the future.

---

## Session-setup decisions (user's answers, 2026-06-12)

1. **Media server — Jellyfin-first.** Verbatim: *"Let's go ahead with option A - jellyfin first approach. But I also want to know the other 2 emby and plex if we take what are the benifits or features we will have. Plan for that also. I dont mind buying all 3. Agree we can go with jellyfin first as thats open source and easy to change it. But also be super detailed steps of what all to change in jellyfin from scratch install to everything"*
   → Deliverables must include: (a) Jellyfin-first roadmap, (b) detailed Emby + Plex delta analysis (what each would add if bought/used), (c) a super-detailed Jellyfin setup guide from scratch install to fully configured.
2. **Scope — Docs + roadmap only.** Deep review, updated ARCHITECTURE/README, architecture graphs (interactive HTML + Mermaid), risk/impact attributes on all IMP tasks, new tiers, phased end-goal roadmap, research dossier, master docs index, resume tracking. No feature code this session.
3. **Pending leftovers — commit both first.** (Done: commit `5000533` carries the README table re-format and `docs/feature-multi-episode/` PLAN+DECISIONS from PR #21.)
4. **Interaction channel — In-client only (Jellyfin/Emby).** Fetch-done notifications and the post-watch "archive?" prompt should be designed to live inside the media-server clients (plugins / session-message API / library constructs), not via phone push, Telegram, or a separate dashboard. Where something is impossible in-client, the roadmap must say so explicitly and may name a fallback — clearly marked as a fallback.

## Follow-up round (2026-06-12, after the initial PR #22 was opened)

User reviewed the PR ("looks good"), then asked for three additions BEFORE merging:

1. **Topology answer + robustness.** Verbatim: *"there are multiple devices but accounts are
   currently only 3 google accounts. One for movies, one for series and one for anime. However its
   true that currently it'll be a single point of failure. Add other ways to make it super robust
   maybe like adding a backup account. Or using Google Photos sharing feature to share to multiple
   backup accounts. Research which way is best and if also main account is gone will still direct
   backup from Google Photos remove from other accounts also? Or we need to manually put into
   multiple backup accounts for this usecase? Also is there anyway we can encrypt and upload so that
   if google starts seeing into our files it'll not be caught in copyright and still work as expected?
   Add these also in a separate tier maybe."*
   → Delivered: **Tier X** (`improvements_tierX.md`, X1–X5) with a §0 research decision table.
   Research verdicts: Google Photos *sharing* is NOT a safe backup (view-only shares vanish for
   everyone if the owner account dies; "saved" copies survive but consume the backup account's
   normal quota and lose the Pixel free-unlimited benefit) → **direct re-upload to backup accounts
   via Pixels signed into them** is the only robust backup (X1). Encryption is feasible via
   encrypt-then-wrap-in-valid-MKV (Matroska attachment, original-quality byte-exact round-trip) but
   gated on an upload-acceptance spike + the change-gate (X3). Urgency driver found in research: a
   **Feb-2026 wave of instant, unrecoverable Google CSAM-AI false-positive bans**.
   → Also surfaced **IMP-C16**: 3 accounts means anime has its own account, but fetch only has 2
   Chrome profiles and mis-routes `ani-*` to the series account — first anime restore would silently
   fail. Topology question (was REVIEW_NOTES §E1) now RESOLVED everywhere.

2. **Always-updated priority list + futuristic interactive graph.** Verbatim: *"add a priority list
   always which will be updated always... which task is suggested to do next. Also tell me which all
   are critical things like bugs which will break which we need to do first. Add them as first on that
   list. Maybe store that list also as a graph with nodes being these tasks and clicking the node
   should directly take it to that task details. Make it super futuristic visually... This list also
   should be updated each time when we add a task or bug or improvement."*
   → Delivered: `improvements/PRIORITY.md` (single source of truth — critical Band 0 first, a
   `👉 SUGGESTED NEXT TASK` pointer, 5 bands, maintenance protocol) + `docs/priority-graph/priority-graph.html`
   (concentric-by-urgency vis.js graph, glassmorphism/neon, click-node→details + jump-to-tier-file,
   search/filters). The "keep both current on every task change" rule is wired into CLAUDE.md,
   improvement_details.md, and docs/README.md.

3. **Then merge if it looks good** — so the PR now carries this follow-up round too; STILL STOP at
   Checkpoint 1 for explicit merge approval.

## Standing constraints (from CLAUDE.md + memories)

- **Auto-rollback change-gate:** any improvement touching rollback behavior must be flagged, never silently altered (`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §10).
- **Unlimited uploads are sacred:** storage redesigns must preserve the 4× Pixel 1 XL parallel original-quality Google Photos upload path.
- **Human gates:** PR merge to `main` (Checkpoint 1) and branch archival (Checkpoint 2) require explicit user approval.
- **Multi-episode invariant:** new code iterating season_map children must resolve/skip `multi_ep_alias` entries.
- **Apple TV-style custom UI** is a long-term goal, sequenced AFTER core CLI consolidation / integrity tooling / JSON output mode (existing `apple_tv_ui_roadmap.md` — to be reconciled with the new roadmap).
