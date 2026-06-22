# Decision Card — Step 2.3 (IMP-E14 Phase 2): Archived "Fetch & Restore" + growing chunk-% progress border + auto-flip

> **LIVE A/B in progress — NOT finalized.** This card RANKS and RECOMMENDS; the
> **USER picks the winner after a live A/B test**. Nothing merges until then. No
> synthesis is performed automatically (a possible graft is noted in §5).
> Live ports: **8765 = Candidate A (CSS conic-gradient ring)**, **8766 = Candidate B (inline SVG outline)**.

---

## 1. Step

- **ID / Title:** Step 2.3 — IMP-E14 Phase 2 — Archived (fetchable) sub-view: working **Fetch & Restore** button → `POST /api/action/fetch_restore {id, options:{episodes?}}` → poll `GET /api/job/{id}` reading `progress {done,total}` → **growing border** around the card proportional to `done/total`, smoothed between chunk ticks, snapping to a glowing closed loop on `done` → **auto-flip** the card out of Archived into Fetched·not-archived (`RESTORED_REPLACE_AGAIN`) via an `/api/items` refresh; on error show captured output and leave it Archived. Accessible numeric label + `prefers-reduced-motion`. Both add an identical `?demo` client-side trigger (synthetic 0→100%→glow, no backend). **The differentiator is the BORDER TECHNIQUE only.**
- **Judge criteria (scored A/B below):**
  1. Progress affordance matches intent — growing border tracking real chunks + buttery smoothing + satisfying complete glow + accessible numeric label & reduced-motion.
  2. Lifecycle auto-flip correctness (Archived → Fetched·not-archived via `/api/items` refresh, no stale/double render — **both reuse the proven Phase-1 refresh path**).
  3. Robust polling/error (re-enable on done OR error; faithful error output; no double-submit; reads `progress{done,total}` incl. `total==0` guard).
  4. Vanilla/no-build cleanliness + XSS-safety + palette coherence + responsiveness (cards reflow; the border must track resize).

---

## 2. Per-candidate findings (independently verified — not just the CRITIQUEs)

### Candidate A — CSS conic-gradient ring
- **Technique:** A single `.fetch-ring` overlay child painted by `conic-gradient(from -90deg, accent calc(var(--progress)*1turn), track 0)`, carved into a hollow rounded rectangle via `mask` + `mask-composite: exclude`. Progress is one registered custom property `@property --progress {syntax:"<number>"}`; smoothing is a `transition: --progress 0.55s cubic-bezier(...)` so the conic sweep tweens between chunk ticks. `done` snaps fraction→1, swaps the conic for a seamless solid ring, fires a one-shot mint glow (a `box-shadow` on the card so `overflow:hidden` doesn't clip the halo).
- **Files + LOC (committed):** `app.js` 533, `card.js` 649, `styles.css` 900. No new file. (data.js/modal.js unchanged.)
- **Independent results:**
  - `node --check` — **all 4 JS pass** (app, card, data, modal).
  - XSS — **clean.** Only a comment mentions `innerHTML`; all dynamic writes go through `textContent`/`renderJob`'s `<pre>`.
  - POST shape — **correct:** `var body = {id: item.id, options: {}}; if (episodes) body.options.episodes = episodes;` (`card.js:372`).
  - Progress read + guard — **correct:** `fractionOf()` reads `progress.total`/`.done`, `if (total <= 0) return 0` (`card.js:296-298`); `done` phase forces `frac = 1` (`card.js:319`).
  - Auto-flip — **correct:** terminal `done` → `scheduleRefresh()` → `load(false)` → `/api/items` (`card.js:433`), NOT `/api/reclaim`. Panel rebuilt via `panel.textContent=""` (`app.js`) — no stale/double render.
  - Double-submit — **guarded:** `if (btn.disabled) return; btn.disabled = true` (`card.js:363-364`); re-enabled on done (`428`) and via `failFetch` on error/network (`455`).
  - TestClient (from A worktree) — `/ /app.js /card.js /data.js /modal.js /styles.css /api/items` all **200**.
  - Live — `http://127.0.0.1:8765/` **200**, `/?demo` **200**, `/card.js` **200**.
- **Self-critique highlights (confidence: high):** Flags `@property` smoothing is browser-gated (snaps per-tick on engines lacking it; numeric label is the always-correct channel); indeterminate phase shows `preparing…`/`…` not a fake spinner; `?demo` force-jumps to the Archived view; glow uses a `box-shadow` keyframe (not purely compositor-driven).
- **Pros vs criteria:** (1) Single faithful `driveBorder()` path for real + demo — honest A/B; reduced-motion drops tween to a static proportional fill. (2/3) Lifecycle and polling are textbook, riding proven Phase-1 machinery. (4) Lightest possible DOM — one CSS element per card, no JS geometry, no observer; nothing to leak; resize is automatic (conic re-reads the box every paint, no JS).
- **Cons / risks:** Smoothing **degrades** (does not break) where `@property` is unsupported (older Safari/Firefox) — on the localhost Chromium/Edge target this is moot. Indeterminate (`total==0`) sits visually at 0 with a textual `preparing…` — honest but momentarily reads as "nothing happening."

### Candidate B — inline SVG rounded-rect outline
- **Technique:** A per-card inline `<svg>` overlay (new module `ring.js`). `createRing()` sizes the SVG to the card's real pixel box (`getBoundingClientRect`), builds a rounded-rect `<path>` (top-centre, clockwise), takes the true perimeter via `getTotalLength()`, sets `stroke-dasharray = perimeter`, and per tick sets `stroke-dashoffset = perimeter*(1 - done/total)` so the outline grows. `transition: stroke-dashoffset 0.55s` smooths ticks; `done` adds `.complete` for a breathing `drop-shadow` glow (eased close, instant under reduced-motion). A `ResizeObserver` re-measures on reflow.
- **Files + LOC (committed):** `app.js` 468, `card.js` 602, `styles.css` 835, **new `ring.js` 218**, `index.html` edited. (data.js/modal.js unchanged.)
- **Independent results:**
  - `node --check` — **all 5 JS pass** (app, card, data, modal, ring).
  - XSS — **clean.** Two comment mentions only; all writes `textContent`/numeric `setAttribute`.
  - POST shape — **correct:** `var options = {}; if (item.episodes) options.episodes = item.episodes; return {id: item.id, options}` (`card.js:369-372`).
  - Progress read + guard — **correct:** `setChunks` does `if (!isFinite(total) || total <= 0) {fraction=0; …; return}` (`ring.js:159`), clamps `doneN>total` mirroring the server (`168`); `getTotalLength` fallback `2*(w+h)` (`ring.js:121`).
  - Auto-flip — **correct:** terminal `done` → `scheduleRefresh()` (`card.js:453`) → `load(false)` → `/api/items`, panel rebuilt via `panel.textContent=""` (`app.js:297`); error path deliberately does NOT refresh (`card.js:455-456`).
  - Double-submit — **guarded:** early-return on `btn.disabled` (`card.js:374`), re-enable on terminal done/error.
  - TestClient (from B worktree) — `/ /app.js /card.js /data.js /modal.js /ring.js /styles.css /api/items` all **200**.
  - Live — `http://127.0.0.1:8766/` **200**, `/?demo` **200**, `/ring.js` **200**.
- **Self-critique highlights (confidence: high):** Honestly flags DOM weight (SVG + 2 paths + ResizeObserver per Archived card); resize/observer cost (`getBoundingClientRect`+`getTotalLength` per layout); **no teardown wired** — `app.js`'s `panel.textContent=""` discards cards without calling the exported `destroy()`, relying on GC of the detached island + a no-op observer callback; eased-close vs literal "snap" is a deliberate reading of the "buttery" directive.
- **Pros vs criteria:** (1) **Exact** perimeter-proportional fill and a precise % that does not distort on non-square cards; symmetric top-centre grow; clean eased close. (2/3) Lifecycle/polling identical-quality to A, same proven path. (4) Layering is careful (`pointer-events:none; z-index` ordering), `getTotalLength` portable via explicit `<path>`; `ResizeObserver` makes resize-tracking explicit and robust.
- **Cons / risks:** Heavier DOM and a `ResizeObserver` per on-screen Archived card. **Leak risk:** `destroy()` exists but is NOT called on re-render — verified at `ring.js:205` and the absence of a `destroy()` call in `app.js`. The detached card + ring + observer form a GC-eligible island (no external roots; observer callback no-ops on a 0×0 box), so in practice it is collected, but it is **not maximally tidy** and would become a real leak if any future caller retains a card reference. Over many fetch/re-render cycles in one long-lived session this is the main durability question for the live test.

---

## 3. Comparison table

| Criterion | A (conic 8765) | B (SVG 8766) |
|---|---|---|
| 1. Progress affordance matches intent (growing border + glow + label) | ✓ | ✓ |
| 2. Lifecycle auto-flip correctness (`/api/items`, no stale/double) | ✓ | ✓ |
| 3. Robust polling/error (no double-submit, `total==0` guard, faithful output) | ✓ | ✓ |
| 4. Vanilla/no-build + XSS + palette + responsiveness | ✓ | ✓ |
| Smoothing robustness across browsers | △ (`@property`-gated; snaps per-tick on old Safari/FF; label always correct) | ✓ (`stroke-dashoffset` transition universal) |
| DOM / perf weight | ✓ (one CSS element, no JS geometry, no observer, nothing to leak) | △ (per-card SVG + ResizeObserver; untorn-down GC island) |
| Accessibility (numeric `aria-live` label + reduced-motion) | ✓ | ✓ |
| Geometry exactness on non-square cards | ✓ (conic angle is naturally proportional) | ✓ (measured perimeter, exact %) |

**Both pass all four core acceptance criteria.** The differences are confined to two trade-off rows — browser-smoothing robustness (B wins) vs DOM/perf weight and leak-tidiness (A wins) — which is exactly the conic-vs-SVG differentiator the step set up. This is a close, taste-weighted call.

---

## 4. How to compare live

URLs (also LAN `192.168.0.90:<port>` / Tailscale `100.110.252.15:<port>`):
- **A (conic):** http://127.0.0.1:8765/  and  http://127.0.0.1:8765/?demo
- **B (SVG):** http://127.0.0.1:8766/  and  http://127.0.0.1:8766/?demo

Checklist (open the two `?demo` URLs side by side):
1. **Watch the border fill 0 → 100% → glow** on each `?demo`. Judge the *buttery feel* between chunk ticks and the *satisfaction of the completion glow*. (A tweens the conic sweep via `@property`; B eases `stroke-dashoffset` to a closed loop then breathes a drop-shadow.) This is the core taste call.
2. **Switch to a category's Archived sub-view** and eyeball **Fetch & Restore button placement** and **poster/border coherence** (does the ring sit cleanly on the rounded card edge over the poster?).
3. **Resize the window** — does the border still track the card box? (A re-reads the box every paint automatically; B re-measures via `ResizeObserver`.) Confirm neither ring detaches from the corner.
4. **Reduced motion** — if you can toggle `prefers-reduced-motion`, confirm both drop to a static proportional fill with the numeric label intact.

**Note — a REAL fetch is NOT triggered in this comparison.** A real `fetch_restore` needs Selenium and mutates the library, so it is out of scope for the A/B. The `?demo` path drives the **exact same** `driveBorder()` / `ring.setChunks`+`ring.complete()` production code with synthetic numbers, so the animation you see is faithful to the real one. The lifecycle auto-flip and error handling were verified by code inspection (both reuse the proven Phase-1 `scheduleRefresh → /api/items` path) rather than live.

---

## 5. Ranked recommendation (TASTE-WEIGHTED — the user's live impression should dominate)

**1st — Candidate A (CSS conic-gradient ring).**
Rationale: identical correctness on all four core criteria, but A achieves it with strictly less machinery — one CSS element driven by one custom property, **no per-card JS geometry, no ResizeObserver, and therefore nothing to leak and no teardown debt**. This is squarely aligned with the project's "Simplicity First / Surgical Changes" guidelines and is the lower-maintenance, lower-risk path for a view that may render hundreds of Archived cards over a long-lived session. Its single weakness (smoothing degrades, never breaks, where `@property` is unsupported) is informational-only — the numeric label is always correct — and is irrelevant on the Chromium/Edge localhost target.

**2nd — Candidate B (inline SVG outline).**
Rationale: equally correct and arguably the more *robust smoothing* across browsers (the `stroke-dashoffset` transition is universal, with no `@property` dependency), plus an exact perimeter-proportional fill. It loses on DOM/perf weight and the **un-wired `destroy()`** (GC-island reliance) — a real, if low-probability, durability concern that A simply does not have.

**Why taste-weighted, not decided:** both satisfy the spec; the genuine differentiator is the *feel* of the border animation and completion glow, which only the live `?demo` can settle. **Defer the aesthetic call to the user's live test.**

**Suggested graft (only if the user wants it — not auto-applied):**
- If the user prefers **A's feel**: optionally adopt **B's universal `stroke-dashoffset` smoothing insight** is N/A (different technique), but consider documenting the `@property` fallback caveat near A's ring CSS.
- If the user prefers **B's feel**: **wire B's exported `ring.destroy()` into the `app.js` panel teardown** (call `destroy()` on each card before `panel.textContent=""`) to remove the GC-island reliance and make the ResizeObserver lifecycle explicit. This is the one concrete improvement B should pick up regardless.

---

## 6. 👉 Your choice

Pick **A**, **B**, or **"winner + graft"** after the live `?demo` comparison:
- **A** (conic) — recommended default: simpler, lighter, no leak surface; smoothing perfect on the target browser.
- **B** (SVG) — if its eased perimeter grow + breathing glow simply *feels* better live; if chosen, graft in the `destroy()` teardown.
- **A + graft** — A as-is plus a one-line note documenting the `@property` smoothing fallback.

**Nothing merges until you choose.** Verification status: **both candidates pass every acceptance criterion of Step 2.3** (node --check ✓, TestClient 200 ✓, live `/` + `/?demo` 200 ✓, XSS-safe ✓, correct POST shape ✓, `total==0` guard ✓, auto-flip via `/api/items` ✓, no double-submit ✓, accessible label + reduced-motion ✓) — so either is a valid winner; the choice is taste + maintenance preference.
