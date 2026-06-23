# Decision Card — Step 3.1 (IMP-E14 Phase 3): aesthetic continuously-animating hover border on `.card`

> **This is a RANK + RECOMMEND card, NOT a final verdict.** Nothing merges. The
> user makes the call after a LIVE A/B on their **iPhone (iOS Safari) + desktop**.
> The single most important dimension — aesthetic "buttery / come-back-to-it"
> feel — can only be judged in motion, which the judge cannot see; so the user's
> live impression dominates. What the judge CAN and DOES decide firmly is the
> **iOS-Safari safety ranking**, which is load-bearing (a blank page = automatic
> loss; step 2.3's conic+`-webkit-mask-composite:xor`+`@property` ring blanked the
> user's iPhone).

## 1. Step

- **ID / title:** Step 3.1 — IMP-E14 Phase 3, "aesthetic continuously-animating hover border on `.card`".
- **Brief:** A highlight border that "keeps moving constantly but in an aesthetic way" (NOT a jarring blink), buttery; must COMPLEMENT (not break) the cursor-follow radial glow (`.card::before`, glow.js) and the SVG fetch progress ring (`.fetch-ring`, during a fetch); `prefers-reduced-motion` static fallback; focus-visible intact; GPU-friendly; vanilla/no-build; and **render correctly on iOS Safari**.
- **The 4 judge criteria:** (1) aesthetic quality + buttery feel [taste-weighted, most important, user-judged]; (2) **iOS-Safari safety** [the load-bearing risk]; (3) performance [compositor-friendly props]; (4) coexistence + a11y [doesn't fight cursor glow / fetch ring; pointer-events:none; reduced-motion; focus-visible; no stuck glow on touch].
- **LIVE A/B (all `--demo` safe mode):** **A (conic ring) → http://127.0.0.1:8765/** · **B (sheen sweep) → http://127.0.0.1:8766/** · **C (dual-glow) → http://127.0.0.1:8767/** (also LAN `192.168.0.90:<port>`, Tailscale `100.110.252.15:<port>`). All three confirmed responding **200** at `/`.
- **iOS stakes:** the user's primary device is the iPhone. Step 2.3 blanked the page there. iOS safety is weighted heavily next to taste.

## 2. Per-candidate findings

### Candidate A — rotating conic-gradient ring (port 8765, `__cand_31a`, f3f03f7)
- **Technique:** A `.card::after` conic-gradient ring; an accent→focus arc travels the perimeter by animating a registered `@property --ring-angle` 0deg→360deg. Hollowed via `mask` + `mask-composite: exclude` gated behind `@supports`, with a composite-free `box-shadow` safe floor underneath.
- **Files / LOC:** `styles.css` only. **+136 / −1** (CSS-only, no JS, no markup).
- **Grep (iOS-risk tokens, comments stripped):** `-webkit-mask-composite` = **0** ✓ · `mask-composite` = **3** (1 real decl `exclude` + occurrences) · `mask:` = **2** · `conic-gradient` = **2** · `@property` = **1** · `@supports` = **2** (gates the masked ring) · `prefers-reduced-motion` = 5 · `pointer-events: none` = 3 · `.fetching` = 2 · `focus-visible` = 4. **Braces balanced 219/219.** Confirmed: **NO `xor`, NO `-webkit-mask-composite`** — the exact step-2.3 trigger keyword is absent; the modern `mask-composite: exclude` IS present and IS `@supports`-gated; `@property` + `conic-gradient` ARE present.
- **node --check:** all 9 JS OK (none modified). **TestClient:** `/ /app.js /styles.css /glow.js /ring.js /api/items` all **200**.
- **Self-critique highlights / confidence:** Confidence **high**. Honestly flags: corner brightness slightly thinner at 1.6px on 14px radius; **no rotation on Safari 15.4–16.3** (static arc on those — degrades to "doesn't move," not "blanks"); two coincident edge treatments (ring + floor) by design; could not verify a live iPhone render.
- **Pros:** Conceptually the most "premium travelling-arc" look; genuinely compositor-friendly (only `--ring-angle` + `opacity` animate); honest 4-rung degradation ladder (full → static arc → box-shadow floor → reduced-motion); surgical CSS-only.
- **Cons / iOS verdict:** **HIGHEST iOS risk of the three.** It is the *only* candidate that still uses `mask` + `mask-composite` + `@property` + `conic-gradient` — the same FAMILY of primitives implicated in the step-2.3 blank, even though the specific banned keyword (`-webkit-mask-composite: xor`) is gone and the masked path is `@supports`-gated with a box-shadow floor. The construction is defensible and *should* be safe, but given the burn history this is the candidate that **must be tested on the iPhone FIRST**, and a blank/glitch there is an automatic loss regardless of how good it looks on desktop.

### Candidate B — traveling sheen sweep (port 8766, `__cand_31b`, 0f0d311)
- **Technique:** A `.card::after` overlay painting four thin (2px) `linear-gradient` "comet" edge strips (top/right/bottom/left), circulated clockwise by animating a 4-value `background-position` one 46px tile per loop, over a faint inset rim glow. **No masks at all.**
- **Files / LOC:** `styles.css` only. **+98 / −0** (CSS-only, no JS, no markup). **Smallest, simplest diff.**
- **Grep (iOS-risk tokens):** `-webkit-mask-composite` = **0** · `mask-composite` = **0** · `mask:` = **0** · `conic-gradient` = **0** · `@property` = **0** · `@supports` = **0** · `prefers-reduced-motion` = 5 · `pointer-events: none` = 3 · `.fetching` = 2 · `focus-visible` = 4. **Braces balanced 215/215.** Confirmed: **ZERO iOS-risk primitives** — nothing but `linear-gradient` backgrounds, `background-position/size/repeat`, `box-shadow`, `opacity`.
- **node --check:** all 9 JS OK (none modified). **TestClient:** all six routes **200**.
- **Self-critique highlights / confidence:** Confidence **high**. Honestly flags: `background-position` is a paint (not pure compositor) — trivial at one-hovered-card scale; comets read as a *train of sparks* not one continuous ribbon; additive corner brightening ("corner spark"); 2.6s/46px are taste calls; no extra micro-polish added (deliberate restraint).
- **Pros:** **LOWEST iOS risk by construction** — uses none of the trap primitives, so it physically cannot reproduce the blank. Smallest, most surgical change. Clean coexistence (untouched glow, z:2 below fetch ring, `.fetching` stand-down). Touch/reduced-motion/focus all gated like the existing glow.
- **Cons / iOS verdict:** **iOS-SAFEST.** The aesthetic risk (taste only): a "comet train" may read as busier / less of a single elegant traveling point than A's arc or C's dot — strictly a live-look call. Corner luminance is not perfectly uniform.

### Candidate C — dual-layer pulse glow + SVG perimeter trace (port 8767, `__cand_31c`, 6b554c4)
- **Technique:** Layer 1 — a breathing `box-shadow` halo on the card itself (`@keyframes cardBreathe`, escapes `overflow:hidden`). Layer 2 — a static inline-SVG `<rect>` (added to the card template) whose bright dash (`stroke-dasharray: 18 82`) is chased around a normalized `pathLength=100` perimeter by an animated `stroke-dashoffset` — the **same SVG-stroke family as the proven P2 fetch ring**. The two run at 2.6s vs 3.2s (out of phase). No JS; `pathLength`+`non-scaling-stroke` avoid the per-card geometry/ResizeObserver the P2 ring needed.
- **Files / LOC:** `index.html` (**+24**, the static SVG markup) **and** `styles.css` (**+160 / −2**). **+184 total — largest diff; the only candidate that adds markup.**
- **Grep (iOS-risk tokens):** `-webkit-mask-composite` = **0** · `mask-composite` = **0** · `mask:` = **0** · `conic-gradient` = **0** · `@property` = **0** · `@supports` = **0** · `prefers-reduced-motion` = 6 · `pointer-events: none` = 3 · `.fetching` = **6** (most thorough fetch coexistence) · `focus-visible` = 4. **Braces balanced 231/231.** Confirmed: **ZERO mask/conic/@property primitives**; uses only SVG stroke + `box-shadow`.
- **node --check:** all 9 JS OK (none modified; markup added but no JS). **TestClient:** all six routes **200** (index.html change serves fine). Self-critique also reports `tests/test_web_endpoints.py` 5 passed.
- **Self-critique highlights / confidence:** Confidence **high**. Honestly flags: corner dash speed not perfectly uniform in px (from `preserveAspectRatio:none` non-uniform stretch; `rx=11` corners render as small ellipses); **`box-shadow` is a paint, not a pure compositor prop** — kept cheap, one card hovered at a time, but the breathing glow is the most expensive thing here; single accent hue (no per-badge-state tint).
- **Pros:** **LOW iOS risk** — the traveling highlight is the *proven* `stroke-dashoffset` family already shipping on iOS via `ring.js`, the safest reading of the iOS hint. Richest "dual-layer / premium" effect (outer breath + inner traveling dot, out of phase). Most thorough fetch coexistence (6 `.fetching` rules, both layers suppressed). Flat `@media` blocks (no nested at-rules) to dodge any older-iOS nested-at-rule parser risk.
- **Cons / iOS verdict:** **iOS-SAFE (proven family).** Two costs vs the others: (1) animates `box-shadow` (paint) for the breathing layer — explicitly permitted by the step but the heaviest of the three on a low-end device; (2) the only candidate touching markup (`index.html`), so slightly larger surface. Corner dash speed non-uniform (decorative, acceptable).

## 3. Comparison table

| Dimension | A (conic ring) | B (sheen sweep) | C (dual-glow + SVG trace) |
|---|---|---|---|
| Aesthetic / buttery feel *(likely — USER decides live)* | ✓ (elegant traveling arc) | ✓ (flowing comet train) | ✓ (richest, dual-layer) |
| **iOS-Safari safety** | **△ (HIGHEST risk — mask+conic+@property family; test FIRST)** | **✓ (LOWEST risk — zero trap primitives)** | **✓ (LOW risk — proven `stroke-dashoffset` family)** |
| Performance (compositor-friendly) | ✓ (angle + opacity only) | ✓ (background-position [paint] + opacity; trivial at 1 card) | △ (box-shadow [paint] breathe + stroke-dashoffset) |
| Coexistence + a11y (glow / fetch ring / touch / RM / focus) | ✓ | ✓ | ✓ (most thorough `.fetching` suppression) |
| Footprint: CSS-only vs markup | ✓ CSS-only (+136) | ✓ CSS-only (+98, smallest) | △ CSS + index.html markup (+184, largest) |

Legend: ✓ strong / △ acceptable-with-caveat / ✗ fails. No candidate scored ✗ on any verified dimension — all three pass node, TestClient (six routes 200), braces balance, and the live 200 check.

## 4. How to compare live

URLs (demo safe mode): **A 8765** · **B 8766** · **C 8767**. Checklist for each:

1. **Desktop (mouse), hover a card:** watch the moving border. Does it "keep moving constantly in an aesthetic way," buttery, not a blink? Compare A's traveling arc vs B's comet train vs C's breathing-glow-plus-traveling-dot. This is the taste call that decides it.
2. **iPhone (iOS Safari) — open each URL and confirm the page does NOT blank / glitch.** **Open A (8765) FIRST** — it is the highest-risk candidate (conic + mask-composite + @property). A blank or render glitch on the iPhone is an automatic loss for that candidate, no matter how good it looked on desktop. Then B and C (both expected safe).
3. **Toggle OS reduce-motion** (iOS: Settings → Accessibility → Motion → Reduce Motion; desktop OS setting) → confirm the animation STOPS and a calm static highlight remains (affordance survives).
4. **Confirm coexistence:** the cursor-follow glow still tracks the pointer; buttons / the ⤢ expand arrow still click; tab to a card → focus-visible outline still shows; on the iPhone, tap a card and lift — the hover effect must NOT get stuck.
5. **Trigger a (simulated, demo-mode) Fetch & Restore** → confirm the hover border YIELDS to the SVG progress ring (the two don't fight on the shared edge), then returns when the fetch ends.

## 5. Ranked recommendation (taste-weighted — user's live impression dominates)

Because the dimension that matters most (aesthetic feel) can only be judged in motion on the user's own devices, this ranking is **provisional on the look** and firm only on the **iOS-safety axis**:

1. **B — traveling sheen sweep** *(safest pick; recommend as the default if the look is acceptable).* Lowest iOS risk by construction (zero trap primitives), smallest/most surgical diff, clean coexistence. If the user likes the comet-train look on desktop, this is the lowest-regret choice for a phone-first user with the step-2.3 burn.
2. **C — dual-layer glow + SVG trace** *(safe + richest look; strong if the user wants more "premium").* iOS-safe via the *proven* `ring.js` stroke family, the most thorough fetch coexistence, and arguably the most "alive / come-back-to-it" effect. Slightly heavier (`box-shadow` paint) and the only one adding markup — minor. Rank above or below B is **purely the user's taste call**; on safety they are effectively tied (B marginally simpler).
3. **A — rotating conic-gradient ring** *(most elegant arc, but the iOS-riskiest — gate on the phone).* Potentially the prettiest single traveling arc, and well-engineered (no `xor`, `@supports`-gated, box-shadow floor). But it is the **only** candidate still in the mask + conic + `@property` family that blanked iOS in 2.3. **Do not ship A unless it renders perfectly on the user's actual iPhone — test it FIRST.** If A both survives the iPhone cleanly AND wins on desktop look, it is a legitimate winner; until the phone confirms, it ranks third on risk.

**Possible graft (no auto-synthesis):** if the user loves A's traveling-arc *look* but A misbehaves on iOS, the same single-traveling-point aesthetic is achievable safely via C's `stroke-dashoffset` technique (a `stroke-dasharray` highlight tuned to read as a longer arc rather than a dot) — i.e. "A's look, C's iOS-safe mechanism." Likewise C's breathing `box-shadow` halo could be grafted onto B as an optional second layer. These are follow-up notes only.

## 6. 👉 Your choice

**Defer to the live A/B.** Judge recommendation: **B or C** (both iOS-safe) as the low-regret default — **B** if you want the simplest/safest, **C** if you want the richer dual-layer feel. **Test A on the iPhone FIRST**; only consider A if it renders flawlessly there AND wins your eye on desktop. Reply with **A**, **B**, **C**, or **"winner + graft"** and the git-agent will proceed. **Nothing merges until you choose.**

### Verification status (judge-confirmed, all three)
- node --check: all 9 static JS **OK** in every candidate (none modified any JS).
- TestClient from each worktree: `/ /app.js /styles.css /glow.js /ring.js /api/items` → **200** (C's index.html change serves fine).
- Live ports: 8765 / 8766 / 8767 all **200** at `/`.
- CSS braces balanced: A 219/219, B 215/215, C 231/231.
- iOS-risk grep matches the brief's expected profile: **A** has mask-composite(exclude)+conic+@property behind @supports (highest risk); **B** has none (lowest); **C** has none, uses SVG stroke (low). No candidate emits `-webkit-mask-composite` or `xor`.
- Each candidate has a `prefers-reduced-motion` static fallback, `pointer-events: none` on the effect layer, a `(hover:hover) and (pointer:fine)` touch gate, a `.fetching` fetch-ring stand-down, and leaves `:focus-visible` untouched.

All three are viable and shippable on the verifiable axes; the decision is the user's taste plus the iPhone render of A.
