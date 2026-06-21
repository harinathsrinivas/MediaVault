# Candidate C Self-Critique — master-detail SPA

## Approach taken

A two-pane master-detail workflow console on a dark (`#0b0e14` near-black, faint cool cast) ground, accented with an electric cyan/teal (`#38e0c8`) that reads as "futuristic but functional" with zero external assets. The HEADER is a single bar: the `Media`**Vault** wordmark, a pill that shows the reclaimable total prominently (`160.84 GB` in cyan, tabular-nums), the four filter chips, a Refresh button, and the global "Sort library" button. Below it, the workspace is a CSS grid: a fixed-width (340px) LEFT RAIL of compact item rows — each row is a glowing badge **dot** + monospace **id** + the human label + right-aligned **size** — and a RIGHT DETAIL pane filling the rest. The rail and the detail pane each scroll independently (`min-height:0` grid children, `overflow-y:auto`). Selecting a row (click, or up/down arrows when the rail is focused) highlights it with a cyan-glow tint + left border and populates the detail pane with the FULL item: a colored **badge pill**, the large monospace **id** (with a "guessed id — edit before prep" amber flag for UNPREPPED), the **size**, a mono **path box**, the read-only **suggested command** with a Copy button, the **suggested folder** as an editable text input + Copy (disabled+annotated when `applies` is false), and the **action button(s)**. The four states read at a glance by a fixed hue each: UNPREPPED=amber, LOCAL_NOT_PUSHED=blue, PUSHED_NOT_ARCHIVED=violet, RESTORED_REPLACE_AGAIN=rose — used consistently on the chip dot, the rail dot, and the detail pill. The **replace confirm** is a centered modal over a blurred dark backdrop with a red-bordered header, the exact copy "This deletes the original after verifying the cloud upload.", the target id, and a red "Delete original & replace" button; only that button POSTs (with `confirm:true`). Running an action swaps in a **job panel** under the actions: a spinner + uppercase status (running=cyan / done=green / error=red) and a `<pre>` of the captured output. The **responsive** behavior: at ≤760px the grid collapses to one column and becomes a drill-in — `body[data-view]` shows the list OR the detail (with a "← Back to list" button and Escape-to-go-back), so a phone acts on one item at a time without a cramped split.

## Design decisions and tradeoffs

- **Drill-in vs. true split on narrow screens.** For master-detail, a 50/50 split on a phone is unusable. I drove single-pane visibility with a `body[data-view="list"|"detail"]` attribute and CSS, rather than JS show/hide of nodes. Alternative considered: always-stacked (list above detail). Drill-in keeps full context for the one selected item and matches the "act on one item with full context" thesis; cost is the extra Back affordance, which I made explicit (button + Escape).
- **Editable provider id as the full folder string, not just the brace token.** `suggested_folder.folder` is e.g. `Title (Year) {tmdb-0000000}`. The API exposes `editable_provider_field` (`tmdb`/`tvdb`) but no split offsets, so robustly isolating just the brace token across all title shapes is fragile. I made the whole folder an editable input (seeded once, persisted per-item in `state.provider`) and the hint names which placeholder to replace. Tradeoff: the user edits inside the full string rather than a dedicated token box; benefit: zero parsing guesswork and Copy always yields exactly what they see.
- **Auto-refresh after a terminal job.** When a job ends, I re-`loadReclaim()` so the rail reflects the new state (an item may change badge or vanish). Tradeoff: a sort that the server mislabels `error` (the known `cmd_sort()→None` server bug) still triggers a refresh — which is harmless and correct (I render the reported status faithfully and do NOT special-case it, per the contract). Selection is preserved across refresh when the id still exists.
- **Single in-flight action guard.** The server is a serialized single-worker queue, so I disable the action buttons + `#btn-sort` while one job polls (`activeJob` flag) and toast if another is attempted. This mirrors the server's one-at-a-time reality and prevents confusing overlapping job panels. Tradeoff: no client-side queueing UI, which is correct for a single-user local console.

## Strengths

- **XSS-safe by construction.** Every dynamic string (path, id, command, folder, job output) is inserted via `textContent` through the `el()` helper (`app.js:38-42`) or `<pre>.textContent` (`app.js:578`); job output uses `<pre>` (`renderJobPanel`, `app.js:570-583`). No `innerHTML`/`insertAdjacentHTML`/`document.write` anywhere (grep-verified). Windows backslash paths render literally.
- **Contract-exact action wiring.** Badge→action map (`app.js:16-21`), body builders `{id, filepath}` / `{id}` (`actionBody`, `app.js:438-442`), and replace POSTing `{id, confirm:true}` ONLY from `confirmReplace` after the modal (`app.js:458-465`). The runner also defensively handles 409/404/non-202 (`app.js:493-512`).
- **Robust poll loop** (`pollJob`, `app.js:527-560`): polls `GET /api/job/{id}` every ~1s via chained `setTimeout`, shows a spinner while `running`, renders status+output on every tick, handles a 404'd job, and resolves on `done`/`error`.
- **Clipboard fallback** (`copyText`/`legacyCopy`, `app.js:74-100`): async API only when `isSecureContext`, else a `textarea`+`execCommand("copy")` fallback — essential on localhost (non-secure), with a toast on failure telling the user to copy manually.
- **`applies` distinction honored** (`suggestedFolderField`, `app.js:347-393`): `applies:true` ⇒ editable input + "replace the {field} placeholder" hint; `applies:false` ⇒ `disabled` input + "folders are never renamed, read-only" annotation.
- **Keyboard master-detail** (`app.js:600-608`): up/down moves selection through visible items and scrolls the row into view; Enter drills in on narrow screens; Escape closes the modal or returns to the list.

## Weaknesses

- **Folder editing is whole-string, not token-scoped.** A user could edit outside the `{…}` braces; I rely on the hint + `editable_provider_field` rather than enforcing edits to only the placeholder. Acceptable given no offsets in the API, but a dedicated token field would be tighter.
- **No focus trap inside the modal.** Tab can leave the modal's two buttons. It is keyboard-dismissable (Escape) and backdrop-click-dismissable, and the destructive button is focused on open, but a full focus trap was out of scope for a self-contained build.
- **`runSortStandalone` is a minor special-case path** (`app.js:660-672`) for "Sort" pressed before any item is selected (so there's a `#job-mount`). It works, but adds a small branch; once an item is selected the normal mount is reused.
- **Spinner-only running state for very fast jobs.** If a job completes within the first poll, the user may see only the terminal panel. Not incorrect — output/status are always rendered — but the "running" affordance can be brief.
- **No automated browser/DOM test.** I could not open a browser per the constraints; correctness rests on `node --check`, the TestClient serve check, tag-balance + sink audits, and code self-review. Runtime DOM behavior (selection, polling) is unexercised by an automated harness.

## Tests run

1. `node --check webui/static/app.js`:
```
v24.16.0
NODE_CHECK_PASS app.js
```

2. In-process TestClient serve check (no port bound; read-only):
```
GET / -> 200
has app.js ref: True
has styles.css ref: True
is my index (Reclaim Console): True
reclaim 200
GET /app.js -> 200 text/javascript; charset=utf-8
GET /styles.css -> 200
```

3. HTML tag-balance + unsafe-sink audit:
```
unclosed tags remaining: []
mismatched end tags: []
OK
--- innerHTML / outerHTML / insertAdjacentHTML / document.write in app.js (expect none) ---
7: * output) are inserted via textContent — never innerHTML — for XSS safety.
(only the explanatory comment; no actual sink)
```

4. Action-mapping / body-shape self-review (grep):
```
439:  if (action === "prep") return { id: it.id, filepath: it.path };
440:  if (action === "push") return { id: it.id };
464:  runAction("replace", { id: id, confirm: true });
17:  { value: "UNPREPPED", ... action: "prep" ...}
18:  { value: "LOCAL_NOT_PUSHED", ... action: "push" ...}
19:  { value: "PUSHED_NOT_ARCHIVED", ... action: "replace" ...}
20:  { value: "RESTORED_REPLACE_AGAIN", ... action: "replace" ...}
```

## Confidence

high

Reasoning for confidence: Every fixed-contract behavior is implemented and statically verified against the real `webui/server.py` and `collect_reclaimable()` shapes (38-item, `160.84 GB` live response): the four action mappings, the ~1s poll loop, the 409-safe replace-confirm flow, the copy buttons with localhost fallback, the filter chips, and the master-detail selection/keyboard wiring. `node --check` passes, the TestClient serves my index + both assets at 200, HTML is balanced, and there are no unsafe HTML sinks. The honest gap is that I cannot exercise runtime DOM behavior in a browser, so selection/polling correctness rests on careful code review rather than an automated UI test — but the logic is straightforward vanilla JS and the data paths are all `textContent`.
