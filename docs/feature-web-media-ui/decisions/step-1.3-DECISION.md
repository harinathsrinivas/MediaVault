# Decision Card — Step 1.3 (IMP-E14, Phase 1): media-type-first web console

**This is a LIVE A/B.** Both apps are running for you to compare in a browser:
**A → http://127.0.0.1:8765/  ·  B → http://127.0.0.1:8766/**

I (the judge) **RANK + RECOMMEND**. I do **not** finalize and I do **not** merge anything. The final aesthetic call is yours after the live test below.

## Step
- **id / title:** IMP-E14 Phase 1, step 1.3 — replace the reclaim-only SPA with a media-type-first console.
- **Shape (identical in both):** top tab bar Movies / TV series / Anime / Others (keyboard-accessible, count badges); selecting a tab shows that category grouped into 5 disk-state sub-views in order **Unprepped → Local·not-pushed → Pushed·not-archived → Fetched·not-archived (RESTORED_REPLACE_AGAIN) → Archived (fetchable)**. Vanilla, no-build, no-CDN. Shared data model (union of `/api/items` incl. ARCHIVED + `/api/reclaim` UNPREPPED + enrichment; category by id prefix; dedupe by id; preserved card affordances + `replace` confirm modal; XSS-safe textContent). **The data model is NOT the differentiator — the LAYOUT is.**
- **The 4 judge criteria:** (1) UX quality of the tab/sub-view structure (clarity, "elegant + smooth", a11y/keyboard, scale to 656 items / 411 series); (2) correctness of category+state grouping vs the shared model; (3) code quality within no-build vanilla; (4) minimal disruption to existing affordances.
- **The only structural difference:** A stacks all 5 sub-views as **collapsible accordions in one scrolling column**; B uses a **segmented sub-nav rail** that shows **one sub-view grid at a time**.

---

## Candidate A — collapsible accordion sub-views
`.candidates/step-1.3/A` · branch `__cand_13a` · commit `6a60492`

**Approach.** Top tablist + one `role="tabpanel"`. The panel stacks all five disk-state sub-views as labelled `<button>`-headed accordions; non-empty sections default open, empty default collapsed, per-section open state remembered for the session. Animated `max-height` that releases to `none` after opening (so an inline job panel can grow without being clipped).

**Modules / size.** 3 JS files, **983 JS lines** (app 398 / card 409 / data 176) + styles.css 697. Leanest of the two. Modal logic lives inside `card.js` (`openConfirmModal`/`wireModal`).

**My independent verification.**
- `node --check`: app.js / card.js / data.js — **all OK.**
- innerHTML grep: only in comments (`card.js:7`, `card.js:318`). All dynamic id/title/path/output via `textContent` (`card.js:139` id, `:147` size, `:150` path, `:352` job `<pre>.textContent`; modal id `card.js:375`). **XSS-safe confirmed.**
- Shared-model logic: union with `/api/items` winning shape + reclaim enrichment (`data.js:97-119`); UNPREPPED sourced only from reclaim `badge==="UNPREPPED"` (`data.js:123-144`); dedupe by id via `byId` guard; category by prefix (`data.js:53-59`). **Matches the contract.**
- `replace` routing: `STATE_META` marks PUSHED/RESTORED `confirm:true` (`card.js:25-26`); `onActionClick` → `openConfirmModal` → POSTs only on confirm (`card.js:232-240, 396-409`). **Confirmed gated.**
- TestClient from the A worktree: `/ /app.js /data.js /card.js /styles.css /api/items /api/reclaim` → **all 200.** Live port 8765 → 200.

**Self-critique highlights (confidence: high).** Drove `data.js` through Node on the **real** 656-item / 35-reclaim payload: 0 dup ids, grouped grand total 680 == merged == sum of tab counts; tab counts `{movies:111, series:411, anime:158, other:0}`; ARCHIVED across cats = 542. Honestly flags two data-quality soft spots: (a) 5 reclaim rows share id `mov-en-0000-sample` and collapse to one card under the mandated dedupe-by-id; (b) **107 `LOCAL_READY` rows** (a non-canonical state) are folded into **Local·not-pushed** via `normalizeState` (`data.js:68-73`) — no data loss, but a heuristic label. Also notes a post-job refresh re-applies default-open to untouched sections, and the accordion's *visual* smoothness is only validatable live.

**Pros mapped to criteria.**
- **(1) UX:** all sub-views visible at a glance; expand/collapse + remembered state is great for **small** categories (Movies/Anime) where you want the whole picture. Full a11y: roving tabindex + Left/Right/Up/Down/Home/End (`app.js:120-143`), accordion headers are real buttons with `aria-expanded`/`aria-controls` over `role="region"`.
- **(2) Grouping:** `normalizeState` guarantees **no row ever disappears** — the 107 `LOCAL_READY` rows stay inside the named 5-view structure. Strongest "nothing vanishes" guarantee.
- **(3) Code:** leanest (983 lines), DOM-free `data.js`, clean module split.
- **(4) Affordances:** copy/folder/action/modal/job-poll copied verbatim; archived poster slot + disabled "Fetch & Restore — coming next".

**Cons / risks.**
- At **411 series** the single column becomes a very long scroll; even collapsed, an expanded Archived(per-cat ~104-400) section is a large grid in-column. The "one screen" couch feel is weaker.
- `LOCAL_READY` shown under a **"Local·not-pushed"** label is slightly inaccurate (those 107 rows aren't literally in that lifecycle state) — graceful but mislabelled.
- Modal in `card.js` (not its own module) — fine, marginally less tidy than B's split.

**Feel at 411-item scale:** dense and complete but scroll-heavy; best when you want to *survey* a category. Series will feel long.

---

## Candidate B — segmented sub-nav rail
`.candidates/step-1.3/B` · branch `__cand_13b` · commit `6754b5f`

**Approach.** Top tablist + a **secondary segmented rail** (`role="tablist"`) listing the 5 sub-views with counts; selecting one segment paints a **single grid** for that (category, sub-view) with a fade/slide swap. Default = first non-empty sub-view. Arrow keys **skip empty segments**. Post-job refresh **preserves the user's current view** unless it emptied.

**Modules / size.** 4 JS files, **1174 JS lines** (app 468 / card 381 / data 261 / modal 64) + styles.css 733. Larger, but the extra surface is the genuinely-new sub-nav state machine + a dedicated `modal.js`.

**My independent verification.**
- `node --check`: app.js / card.js / data.js / modal.js — **all OK.**
- innerHTML grep: only in a comment (`card.js:339`). Dynamic data via `textContent` (`card.js:128,133,137,145,156`, job `<pre>` `:373`; modal `modal.js:26,32`). **XSS-safe confirmed.**
- Shared-model logic: `loadModel` unions `/api/items` (shape) + reclaim (enrichment) (`data.js:139-145`), UNPREPPED only from reclaim badge (`data.js:149-155`), dedupe via `seen` map, category by prefix (`data.js:109-115`). **Matches the contract.** Adds a hero "Reclaimable" total using the server string verbatim.
- `replace` routing: PUSHED/RESTORED `confirm:true` (`data.js:58,65`); `onActionClick` → `openConfirmModal` (from `modal.js`) → POST only on confirm (`card.js:246-254`, `modal.js:18-40,49-55`). **Confirmed gated.**
- TestClient from the B worktree: `/ /app.js /data.js /card.js /modal.js /styles.css /api/items /api/reclaim` → **all 200.** Live port 8766 → 200.

**Self-critique highlights (confidence: high).** 33 Node assertions on the merge model pass (hand-traced multi-state example incl. an odd `local_ready` row, an enrichment join, a non-UNPREPPED reclaim row correctly dropped). Honest caveats: (a) per-card `humanSize` rounds at ≥100 (`275 MB`) like the inherited original, while the **hero total uses the exact server string** — cosmetic; (b) more clicks than the accordion to see every sub-view (the deliberate "one screen per selection" tradeoff); (c) ~110 ms swap latency (skipped under `prefers-reduced-motion`).

**Grouping nuance I verified directly:** B does **not** fold unknown states into an existing bucket. `subViewStatesFor` (`app.js:55-63`) appends any present-but-unknown state (e.g. `LOCAL_READY`) as its **own extra segment after Archived**, reachable and counted. So the 107 `LOCAL_READY` rows surface as a distinct, honestly-labelled `LOCAL_READY` sub-view rather than being relabelled "Local·not-pushed".

**Pros mapped to criteria.**
- **(1) UX:** focused single grid per selection is the **better couch-UI direction** and scales cleanly — a 411-series category never produces a giant scroll; you pick a sub-view and see only it. Empty segments greyed + skipped by arrows (`app.js:176-190, 224-252`). Post-job view-preservation (`app.js:380-421`) is a real refinement over A.
- **(2) Grouping:** same correctness, plus unknown states get an **accurate** label as their own segment (no relabelling). Trade: introduces a raw `LOCAL_READY` sub-view name the spec's "5 views" didn't enumerate.
- **(3) Code:** cleanest module split (dedicated `modal.js`), shared `handleBarKeydown` reused by both bars. More lines, but justified.
- **(4) Affordances:** identical preservation; archived poster slot + disabled Phase-2 stub; adds a secondary id line under the title.

**Cons / risks.**
- Seeing all 5 sub-views of a category requires clicking through segments — worse for *surveying* a small category than A's all-open accordion.
- The appended raw `LOCAL_READY` segment label is honest but un-pretty (a real-data wrinkle the user will see today, since there are 107 of them across series/anime).
- Largest footprint + a 110 ms transition that queues under rapid clicking.

**Feel at 411-item scale:** smooth and contained — pick "Archived", get one focused grid; the long-scroll problem A has simply doesn't arise. This is where B pulls ahead.

---

## Comparison table

| Criterion | A (accordion, :8765) | B (segmented rail, :8766) |
|---|---|---|
| 1. UX / clarity / "smooth" / a11y | ✓ (full a11y; great for small cats) | ✓ (full a11y; focused, couch-UI feel) |
| 2. Grouping correctness vs shared model | ✓ (no row ever dropped; 680==680) | ✓ (same; unknown state honestly labelled) |
| 3. Code quality (no-build vanilla) | ✓ (leanest, 983 lines) | ✓ (cleanest split incl. modal.js, 1174 lines) |
| 4. Minimal disruption to affordances | ✓ (verbatim) | ✓ (verbatim) |
| **Scales to 411 items?** | △ (long scroll; collapse helps) | ✓ (one focused grid per sub-view) |
| Module count / complexity | 3 files / lower LOC | 4 files / more LOC, more state |
| Unknown `LOCAL_READY` (107 rows) | △ folded into "Local·not-pushed" (relabelled) | △ own appended `LOCAL_READY` segment (honest, raw label) |

Both pass every hard gate (`node --check`, XSS-safe textContent, `replace` modal-gated, TestClient 8/8 + 7/7 → 200, both live ports up). **Neither has a correctness defect.** The differences are taste + scale + how each handles the 107 non-canonical rows.

---

## How to compare live
Open **A → http://127.0.0.1:8765/** and **B → http://127.0.0.1:8766/** side by side and exercise:

1. **Tabs + badges.** Click through Movies / TV series / Anime / Others on each. Watch the count badges (expect Movies 111 / Series 411 / Anime 158 / Others 0).
2. **Volume handling — the core test.** Open **Movies (111)** then **TV series (411)** on each. Judge how the LAYOUT copes with volume: A = scroll one long column, expand/collapse the 5 sections; B = pick one sub-view segment, see a focused grid. **This is the decision.**
3. **The new data path — Archived (542).** Open the **Archived (fetchable)** sub-view on each (A: scroll to / expand the Archived accordion; B: click the Archived segment). Check the poster-slot placeholder (gradient + initial) and the **disabled "Fetch & Restore — coming next"** affordance.
4. **Keyboard.** Tab into the top tab bar, use Left/Right (and Home/End) arrows. On B, also arrow across the sub-nav rail and confirm it **skips empty (greyed) segments**.
5. **Affordances intact.** On a non-archived card confirm the **Copy** buttons (command + folder) flash ✓, and **carefully open the `replace` modal** on a Pushed/Fetched card — then **Cancel / Esc** (do NOT confirm; it deletes the original).

---

## Ranked recommendation

**1st — Candidate B (segmented sub-nav rail).**
**2nd — Candidate A (collapsible accordion).**

Rationale: both are correct, accessible, XSS-safe, and preserve every affordance — they tie on criteria 2–4. The tiebreaker is **criterion 1 at real scale**, which the step explicitly weights ("scale to a 656-item real library, series 411"). B's one-sub-view-at-a-time rail keeps every category — including 411-series and the 542 archived items — to a single focused grid, which is the smoother, more "couch-UI" experience the IMP-E14 direction is heading toward, and it adds two genuine refinements (post-job view-preservation, skip-empty arrow nav). A is leaner and is actually *nicer for small categories* (Movies/Anime) where seeing all five sub-views open at once is pleasant, but it pays for that with a long scroll on Series and a relabelling of the 107 `LOCAL_READY` rows.

**This is a taste-weighted call — your live impression should dominate.** If, in the browser, the accordion's "everything visible" survey feel beats the rail's focus for how *you* actually triage, A is a fully valid pick.

**Graft notes (for whichever you pick — nothing is merged now):**
- If you pick **B**, consider grafting **A's lower line count / leaner `data.js`** sensibility and, more importantly, decide how `LOCAL_READY` should read: B's raw segment label is honest but ugly; A's relabel is prettier but inaccurate. The ideal is B's separate segment **with a friendly label** (e.g. "Local (ready)") rather than the raw `LOCAL_READY`.
- If you pick **A**, graft **B's post-job view-preservation** (`app.js:380-421`) so a finished job doesn't re-apply default-open to sections the user was mid-triage on (A's own self-critique flags this), and **B's dedicated `modal.js`** split for tidiness.
- Either way: the **107 `LOCAL_READY` rows are real and visible today** — whichever layout wins, agree on how that bucket is named/placed before Phase 2.

---

## 👉 Your choice
Test both live (8765 = A accordion, 8766 = B segmented rail) using the checklist above, focusing on step 2 (how 411-series and 542-archived *feel*) and the `LOCAL_READY` bucket. Then tell me your pick:

- **A** (accordion), or
- **B** (segmented rail — my recommendation), or
- **"winner + graft"** (e.g. "B, but rename the LOCAL_READY segment" or "A, but add B's view-preservation").

**Nothing merges until you choose.** I have not finalized a winner and will not touch any code.
