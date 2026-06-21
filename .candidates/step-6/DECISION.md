# Decision: Step 6 — IMP-E12 web console FRONTEND single-page app (`webui/static/{index.html,app.js,styles.css}`)

## Outcome
Winner: Candidate A
Branch: candidate branch for step-6 / A (mission-control data table)

> Advisory note: this design space is partly subjective and a human will visually inspect all three. This document makes the **correctness + safety** analysis objective and code-cited so the human can weigh the at-a-glance-clarity argument against what they actually see rendered. All three candidates are CORRECT and SAFE on the contract; the winner is chosen on criterion 1 (at-a-glance state + next-command clarity), which the table form serves best.

## Step requirements
On load `fetch('/api/reclaim')` and render. Header shows the reclaimable-GB total (`total_reclaimable_human`/`total_reclaimable_bytes`) + filter chips for the four badges (UNPREPPED / LOCAL_NOT_PUSHED / PUSHED_NOT_ARCHIVED / RESTORED_REPLACE_AGAIN) toggling item visibility client-side. Per item: badge pill, id + path, size (from `size_bytes`), a read-only suggested command (`item.suggested_command`) + Copy button, the suggested folder (`item.suggested_folder.folder`) with an editable provider-id field + Copy (distinguish `applies:true` = NEW-item editable suggestion vs `applies:false` = existing folder, informational). Action buttons mapped by badge: UNPREPPED→prep `{id,filepath:path}`; LOCAL_NOT_PUSHED→push `{id}`; PUSHED_NOT_ARCHIVED & RESTORED_REPLACE_AGAIN→replace (gated); plus a global "Sort library"→sort `{}`. On action: POST `/api/action/{name}` → 202 `{job_id}` → poll `GET /api/job/{job_id}` ~1s until status `done`/`error`, show status+`output` inline. `replace` MUST be gated by an unmissable confirm modal (copy exactly: "This deletes the original after verifying the cloud upload.") and only then POST `{id, confirm:true}` (no-confirm replace = 409 server-side). Dark, responsive, self-contained (no framework/build/CDN/external asset).

## Judge criteria applied (priority order, from the plan)
1. Clarity of the four-state distinction + the suggested next command at a glance.
2. Destructive-action safety in the UX (replace confirm unmissable; never POSTed without `confirm:true`; exact modal copy).
3. "Futuristic but functional" aesthetic + responsiveness; no heavy assets.
4. Accessibility + code simplicity (vanilla, keyboard-usable, XSS-safe stdout via textContent/`<pre>`).

## Candidate summaries

### Candidate A — mission-control data table
- Approach: one dense sortable table; each item is a row (State pill + colored left rail, ID/Path, Size, read-only command+Copy, editable folder+Copy, per-row Action cell with an inline job log); sticky header carries the GB readout + per-badge filter chips with counts + global Sort; arrow-key row navigation with Enter to fire the focused row's action.
- Files modified: `webui/static/index.html`, `webui/static/app.js` (733 lines), `webui/static/styles.css` (664 lines); added `CRITIQUE.md`.
- Tests: `node --check` OK; TestClient `GET /`→200 serving its index, `/api/reclaim`→200. No runtime browser test (constraint).
- Self-critique highlights: honest about (a) item-size formatted client-side from `size_bytes` so a borderline digit may differ from the server's `human_readable_size` (header total still uses the server string verbatim); (b) NO auto-refetch after an action (deliberate, to keep the inline job log visible); (c) positional keyboard focus index re-clamps on re-sort; (d) `color-mix()` pill tints fall back on very old engines.
- Independent assessment:
  - Strengths:
    - **Best at-a-glance scan (criterion 1).** A table puts state + id + size + the suggested command + the action ALL on one row across many items at once; the suggested command sits in its own `Suggested command` column (`buildCommandCell`, `app.js:345`), so an operator reads "what to run next" for every item without clicking. Each state carries a fixed hue on BOTH the pill and a 3px row left-rail (`buildRow` sets `--row-color`, `app.js:439`; CSS `border-left: 3px solid var(--row-color)`), the fastest possible state-scan cue in a dense list.
    - Replace gate is structurally airtight: `confirm:true` is added ONLY inside `fireAction` after `confirmReplace` resolves true; a cancel `return`s before any POST (`app.js:399-407`). Modal copy is exact (`index.html:77`). Default focus on Cancel (`app.js:270`), Esc + backdrop cancel (`app.js:258-262`).
    - Both replace badges route to replace via the single declarative `ACTION_FOR_BADGE` table (`app.js:46-59`); bodies exact: prep `{id, filepath: it.path}` (`app.js:36`), push `{id}` (`app.js:43`), sort `{}` (`app.js:661`).
    - Job stdout rendered via `el("pre", { text: body })` → textContent (`renderJobLog`, `app.js:199`) — XSS-safe.
    - `applies` distinction explicit: folder input `disabled` + "existing · read-only" note when false, editable + "new · edit {field}-id" note when true (`buildFolderCell`, `app.js:351-375`).
    - Real keyboard model: rows `tabindex=-1`, ↑/↓ move a focus highlight with `scrollIntoView`, Enter fires the focused row's primary action; the handler bails when focus is in an input or the modal is open so typing a provider id is never hijacked (`setupKeyboard`, `app.js:530-555`). Sortable columns (size desc default, badge by workflow rank).
    - Self-contained: no external font/CDN/asset (CSS grep clean); responsive — under 860px the table collapses to stacked labelled cards via `data-label` `::before` (`styles.css:634-664`) while staying ONE list.
  - Weaknesses:
    - Load-error banner uses `innerHTML` with an interpolated `String(e.message||e)` (`app.js:712`). This is the app's OWN fetch-error string (e.g. `/api/reclaim -> 500`), NOT captured stdout, so it is not a stdout-XSS sink — but it is the only non-literal innerHTML in the field and is a minor code-cleanliness wart (could be textContent).
    - The `el()` helper carries an unused `html` key (`app.js:88`); grep confirms it is never called with data, so it is dead capability, not a live sink — but it is speculative surface that a stricter reading of "simplicity first" would drop.
    - No auto-refetch after an action: a row's badge can be stale until manual reload (deliberate trade to preserve the inline log).
    - Largest file of the three (733 JS / 664 CSS) — the keyboard/sort machinery is real added surface.

### Candidate B — card grid
- Approach: responsive `auto-fill minmax(340px,1fr)` card grid; each item a tile with a state-tinted gradient poster-placeholder (huge translucent initial), color-coded badge, id/size/path, command+Copy, editable folder+Copy, one large action button, inline job panel; frosted sticky header with a gradient hero GB stat + chip row + global Sort. Uses a `<template>` clone per card.
- Files modified: `webui/static/index.html`, `webui/static/app.js` (592 lines), `webui/static/styles.css` (605 lines); added `CRITIQUE.md`.
- Tests: `node --check` OK; TestClient `GET /`→200, `/api/reclaim`→200; custom HTML tag-balance parser clean.
- Self-critique highlights: forthright that the editable provider-id field is display/copy-only (no action consumes a folder in the contract); guessed-id is not editable into the prep body; filter empty-state not aria-announced.
- Independent assessment:
  - Strengths:
    - Most "polished app / futuristic" feel and the most genuinely intrinsic responsiveness: `auto-fill minmax` reflows with no media query, plus a clean 1-up at 420px (`styles.css:220-224`). ≥44px action targets.
    - Cleanest XSS posture: `<template>` clone + textContent/`input.value` only; ZERO `innerHTML` anywhere in app.js (grep: only comment lines). Job stdout via `<pre>.textContent` (`renderJob`, `app.js:444`).
    - Single source of truth `BADGE_META`/`BADGE_ORDER` (`app.js:23,55`) drives label/hue/action/verb/confirm; bodies exact in `bodyForAction` (`app.js:329-335`); both replace badges `confirm:true`.
    - Replace safe: modal-only POST via `onActionClick`→`openConfirmModal`→callback (`app.js:337-346`); exact copy (`index.html:50`); Cancel-focused, Esc + backdrop cancel.
    - `applies` honored: disabled + "never renamed" note vs editable + "edit the {field} id" note (`buildCard`, `app.js:284-299`).
  - Weaknesses:
    - **Weakest at-a-glance density (criterion 1).** Cards are visually rich but low-density: the suggested command lives inside each tile, so comparing "what to run next" across many items means scanning a grid of large tiles rather than a column. The poster-placeholder + big initial is decorative space that pushes the load-bearing command/action lower in each card. For an OPERATOR console whose top job is "see each item's state + next command fast," the IA spends pixels on aesthetics over scan-throughput.
    - The poster initial derived from the id slug (`initialFor`, `app.js:93`) is essentially decorative and adds code with no operational signal.
    - No keyboard item-navigation model beyond native Tab (chips/buttons are reachable, but there is no list traversal).

### Candidate C — master-detail
- Approach: two-pane — left filterable rail of compact rows (badge dot + id + size), right detail pane rendering the SELECTED item in full (pill, id+guessed flag, size, path box, command+Copy, editable folder+Copy, action button, inline job panel); arrow-key rail navigation; collapses to a single-column drill-in with a Back button + Esc at ≤760px; auto-refetches after a terminal job; single in-flight action guard; toast for copy feedback.
- Files modified: `webui/static/index.html`, `webui/static/app.js` (660 lines), `webui/static/styles.css` (631 lines); added `CRITIQUE.md`.
- Tests: `node --check` OK; TestClient `GET /`→200, `/api/reclaim`→200; tag-balance + sink audit clean.
- Self-critique highlights: honest that folder editing is whole-string not token-scoped; no modal focus trap; `runSortStandalone` is a minor special-case branch; running affordance can be brief for fast jobs.
- Independent assessment:
  - Strengths:
    - Best per-ITEM focus + the only auto-refresh after a terminal job (`pollJob` calls `loadReclaim()`, `app.js:546-547`) so the rail reflects new state — closes A's stale-badge gap.
    - Most defensive runner: explicit 409/404/non-202 branches with readable messages (`app.js:493-512`); single in-flight guard (`activeJob`, `app.js:472-477`) matching the server's one-at-a-time reality.
    - Replace safe: `runAction("replace", {id, confirm:true})` ONLY from `confirmReplace` (`app.js:459-465`); exact modal copy (`index.html:67`); Esc closes modal then drills back.
    - Strong keyboard master-detail: ↑/↓ move selection + scroll into view, Enter drills in, Escape closes/returns (`app.js:612-629`). XSS-safe via `el()` textContent + `<pre>.textContent` (`app.js:573`); zero innerHTML.
    - `applies` honored with the clearest copy ("folders are never renamed, read-only") (`suggestedFolderField`, `app.js:375-410`).
  - Weaknesses:
    - **Hurts at-a-glance comparison (criterion 1).** The suggested command + action live in the DETAIL pane and are visible for ONE selected item at a time; the rail shows only badge dot + id + size, NOT the command. To answer "what do I run for each item" the operator must click through items one by one. The IA optimizes "act on one item with full context," which is the opposite of the plan's stated top priority (see each state's next command AT A GLANCE without drilling).
    - Auto-refetch after a job re-runs `loadReclaim()`, which on a server-mislabeled-`error` sort still refreshes (harmless, acknowledged) but also discards the just-shown job panel by re-rendering the detail — the inline result is shorter-lived than A's.
    - `runSortStandalone` (`app.js:634-645`) is an extra special-case branch for "Sort pressed with nothing selected."

## Head-to-head comparison

**A vs B.** Both are correct and safe. On criterion 1, A wins clearly: a table renders state + id + size + suggested command + action for MANY items simultaneously in aligned columns, while B's card grid is lower-density and spends prime pixels on a decorative poster/initial, so cross-item "what to run next" scanning is slower. On criterion 3, B wins: `auto-fill minmax` is more elegantly responsive than A's table→stacked-card collapse, and B's frosted/gradient look is the more obviously "futuristic" of the two. On criterion 4, B is slightly cleaner (zero non-literal innerHTML; `<template>` clone), whereas A has one non-literal innerHTML in the load-error banner (app-internal string, not stdout) and an unused `html` helper key. A counters with a real keyboard list-navigation model and column sorting that B lacks. Net: A wins the highest-weight criterion (1) and ties/leads on 2; B leads on 3 and marginally on 4.

**A vs C.** Both correct and safe; C is the more defensive runner (explicit 409/404 handling, single-flight guard) and is the only one that auto-refreshes the report after a job, fixing A's stale-badge weakness. But on criterion 1 — the top priority — C is the weakest of the three: its rail deliberately hides the suggested command and action behind a per-item selection, so an operator cannot see each item's next command at a glance; they must drill in one item at a time. A surfaces the command for every row at once. Since criterion 1 outranks the maintainability/robustness edges C holds, A wins the pairing; C's auto-refresh and defensive runner are the things A should borrow.

**B vs C.** Both correct and safe. B (card grid) shows each item's command/action without selection but at low density; C (master-detail) shows them at high fidelity but only one at a time. For "see each state's next command at a glance," B beats C because C requires a click per item. C beats B on robustness (auto-refresh, single-flight guard, 409/404 branches) and on per-item depth. Aesthetically both are strong and modern; B's grid is the more immediately "app-like." Neither is the overall winner: B trails A on density-of-scan, C trails both on at-a-glance command visibility.

## Rationale for chosen winner

Candidate A wins because it best satisfies the **highest-weighted** judge criterion: clarity of the four-state distinction AND the suggested next command **at a glance**. The data-table IA is the only one of the three that puts state (pill + colored row rail, `app.js:439` + `styles.css`), id/path, size, the read-only `suggested_command` (its own column, `buildCommandCell`, `app.js:345`), and the badge-mapped action (`buildActionCell`, `app.js:377`) on a single aligned row for every item simultaneously. An operator answering "what do I run next, for which items, in what state" reads the whole answer by scanning columns — no clicking (C requires per-item drill-in) and no low-density tile-hopping (B). For a reclaim OPERATIONS console, throughput of that scan is the product's core job, and the table form maximizes it.

On criterion 2 (safety), A is fully airtight and ties the others: `confirm:true` is appended ONLY inside `fireAction` after the modal resolves true, with an early `return` on cancel (`app.js:399-407`); the modal copy is verbatim ("This deletes the original after verifying the cloud upload.", `index.html:77`); Cancel is default-focused; Esc and backdrop cancel. All four action bodies are contract-exact via the single `ACTION_FOR_BADGE` table, and both PUSHED_NOT_ARCHIVED and RESTORED_REPLACE_AGAIN route to gated replace. Job stdout renders through `<pre>.textContent` (`renderJobLog`, `app.js:199`), so captured filenames/paths cannot inject markup.

A is honestly **worse** than the others in three concrete ways. (1) It is the only candidate with a non-literal `innerHTML` — the load-error banner interpolates `e.message` (`app.js:712`); this is the app's own fetch-error string, not server stdout, so it is not a stdout-XSS sink, but B and C avoid even this and A also ships an unused `html` key in its `el()` helper (`app.js:88`) that "simplicity first" would prune. (2) It does NOT auto-refetch after an action, so a row's badge can go stale until reload — C solved this. (3) It is the largest implementation (733 JS / 664 CSS), carrying real sorting + keyboard machinery.

Those weaknesses are acceptable given the priorities. The innerHTML instance is provably not a stdout sink (the stdout path is textContent), so it does not breach criterion 2/4's hard XSS rule — it is a cleanliness nit, trivially fixable, not a correctness defect. The stale-badge / no-auto-refresh choice is a deliberate UX trade (keep the inline job log visible) and is recoverable by a manual reload; it does not violate any contract clause. And the extra size buys the keyboard-navigation + sorting that directly serve the at-a-glance-operator thesis. None of these outweigh A's decisive lead on criterion 1, where B and C each structurally sacrifice cross-item command visibility (B to aesthetics/density, C to per-item drill-in).

## Why not the others?

**Candidate B (card grid).** Excellent, safe, and the most visually "futuristic" with the cleanest responsiveness (`auto-fill minmax`, `styles.css:220`) and the strictest XSS posture (zero non-literal innerHTML, `<template>` clone). It loses on the top criterion: the card form is low-density and spends prime vertical space on a decorative poster-placeholder + big initial (`initialFor`, `app.js:93`) that carry no operational signal, so scanning "each item's state + next command at a glance" across many items is slower than a table. Great choice if the human prioritizes aesthetic polish over scan throughput — but the plan ranks at-a-glance clarity first.

**Candidate C (master-detail).** The most robust runner (explicit 409/404/non-202 branches, single-flight `activeJob` guard) and the ONLY one that auto-refreshes the report after a terminal job — genuinely the best engineering on the action path. But its IA directly contradicts criterion 1: the rail intentionally shows only badge dot + id + size and hides the suggested command + action inside the detail pane for one selected item at a time (`renderRail`, `app.js:210-243` vs `renderDetail`, `app.js:287-346`). An operator cannot see each state's next command without clicking item-by-item, which is the exact opposite of "without reading docs / at a glance." Its robustness wins (auto-refresh, defensive runner) are the strongest follow-up candidates to fold into A.

## What we keep from losing candidates (follow-up suggestions — NOT auto-synthesized)
- From **C**: auto-refetch `/api/reclaim` after a terminal job so a row's badge cannot go stale (A's acknowledged gap) — adapt to refresh WITHOUT discarding the just-shown inline job log. Also C's explicit 409/404/non-202 runner branches and the single-in-flight-action guard are worth porting to A's `runAction`.
- From **B**: the `<template>`-clone + strictly-textContent rendering pattern (eliminates A's only non-literal innerHTML at `app.js:712` and the unused `html` key at `app.js:88`); and B's `auto-fill minmax` could inform a denser-yet-pretty responsive mode.
- General cleanup for the winner: convert the load-error banner to `textContent`/DOM nodes and delete the unused `el()` `html` branch.

## Verification status
Confirmed — Candidate A satisfies all acceptance criteria:
- Fetches `/api/reclaim` on load and renders; header shows the server's `total_reclaimable_human` verbatim (`renderHeader`, `app.js:674-679`).
- Four filter chips per badge toggle client-side visibility with live counts (`buildChips`/`toggleBadge`, `app.js:569-598`).
- Per item: badge pill, id+path, size from `size_bytes`, read-only command+Copy, editable folder+Copy with `applies` true/false distinction (`app.js:285-375`).
- Action mapping exact: prep `{id,filepath:path}`, push `{id}`, replace (both badges) `{id,confirm:true}` modal-gated, global sort `{}` (`ACTION_FOR_BADGE`, `app.js:31-60`; `fireAction`, `app.js:399-407`; `setupSortLibraryButton`, `app.js:646-668`).
- 202→`{job_id}`→poll `GET /api/job/{id}` every ~1s until `done`/`error`, status+output rendered inline via textContent (`pollJob`/`renderJobLog`, `app.js:188-225`).
- Replace gated by an unmissable modal with EXACT copy; never POSTed without `confirm:true`.
- Dark, responsive (≤860px collapse), self-contained (no external asset; CSS grep clean). `node --check` OK; TestClient `GET /`→200 serving its index, `/api/reclaim`→200.
