# Candidate A Self-Critique — Mission-Control Data Table

## Approach taken
A single dense, sortable, keyboard-navigable data table styled as a NASA/Bloomberg-terminal operator console. Every reclaim item is ONE table row; a sticky header carries the headline reclaimable-GB readout (large monospace green digits with a soft glow) on the right, a filter-chip strip below it (one chip per badge + an ALL toggle + the global "Sort library" button), and a keyboard-hint line. The shared replace modal is a hard-red dialog over a blurred backdrop.

**Visual design, concretely.** The palette is a deep terminal black-blue (`#0a0e14`) with a cyan accent (`#36c6ff`) and `tabular-nums` monospace for every numeric. Columns: **State** (a colored badge pill + a 3px left rail in the same color) · **ID / Path** (monospace id over a dimmed break-all path; a guessed UNPREPPED id renders amber with a dotted underline and a `guess` tag) · **Size** (right-aligned, number + dimmed unit, e.g. `7.41 GB`) · **Suggested command** (read-only code in a bordered field with an inline `COPY` button that flashes green `COPIED`) · **Suggested folder** (an editable provider-id input + `COPY`; when `applies=true` it is editable with a green `new · edit tmdb-id in { }` note, when `false` it is disabled and annotated `existing · read-only`) · **Action** (the per-row primary button + an inline job log). Rows alternate two near-black shades, hover lifts to a blue tint, and the focused row gets a cyan inset ring.

**How the 4 states read at a glance:** each state owns a fixed hue carried on BOTH the pill and the row's left rail — UNPREPPED = amber, LOCAL·NOT-PUSHED = cyan, PUSHED·NOT-ARCHIVED = violet, RESTORED·REPLACE = rose. The action button's bottom border echoes that hue (and the destructive Replace button is additionally a dark-red theme), so the eye maps color→state→action without reading text. Counts live in each filter chip.

**Replace confirm:** clicking Replace opens a centered modal with a red border + red glow over an 82%-opacity blurred backdrop, a `!` icon, the warning "This deletes the original after verifying the cloud upload.", the target id in a framed mono box, and Cancel (default focus) / "Delete original" buttons. Backdrop-click and Esc cancel. Only the confirm button resolves the promise true, after which the POST adds `confirm:true`.

## Design decisions and tradeoffs
- **Colored left-rail mirrors the pill hue.** I encode each badge on two surfaces (pill + 3px rail + action-button underline) rather than relying on the pill alone. Alternative: pill-only (cleaner, less redundant). I chose redundancy because in a dense single-color-background terminal table, a per-row color stripe is the fastest scan cue — the whole point of the mission-control IA.
- **Keyboard model: rows are `tabindex=-1`, driven by ↑/↓ + Enter; editable fields/buttons keep native Tab.** Alternative: make every row part of the natural Tab order. I rejected that because Tab would then cycle through dozens of rows before reaching a field. Instead Arrow keys move a focus highlight on rows (with `scrollIntoView`), Enter fires the focused row's primary action, and Tab still reaches the provider inputs and copy/action buttons normally. The keydown handler explicitly bails when focus is in an input/textarea or the modal is open, so typing a provider id is never hijacked.
- **Copy uses `navigator.clipboard` with an `execCommand('textarea')` fallback.** The server is explicitly offline/localhost (plain `http`), where `navigator.clipboard` is often unavailable (non-secure context). Without the fallback, Copy would silently no-op on the exact deployment target. Tradeoff: a few extra lines + a deprecated API path, accepted for reliability.
- **Sort defaults to size-descending; badge sort uses a fixed rank.** Largest reclaim first is the operator's natural priority. Badge sorts by a workflow-order rank (UNPREPPED→…→RESTORED) rather than alphabetically, which is more meaningful. Clicking an active header flips direction; an arrow indicator (▼/▲) shows state.
- **Job output rendered via `textContent`/`pre`, never `innerHTML`.** Captured `cmd_*` stdout is untrusted text; injecting it as HTML would be an XSS vector. All dynamic strings (ids, paths, output, error details) go through `textContent`. Only the static banners use `innerHTML` with literals.
- **`sort` is rendered faithfully as whatever the job reports.** Per the contract I did NOT special-case the known server bug where a successful `sort` is marked `error` (because `cmd_sort()` returns `None`). The global sort log shows the real status/output verbatim.

## Strengths
- 409-safe replace gate is structurally enforced: `confirm:true` is added ONLY inside `fireAction` after `confirmReplace` resolves true (`app.js` `fireAction`), and the modal's confirm button is the only thing that resolves true (`app.js` `closeModal`/`confirmReplace`). A cancel returns before any POST.
- All four badge→action mappings are a single declarative table `ACTION_FOR_BADGE` (`app.js`), with bodies built per the contract: prep `{id, filepath}`, push `{id}`, replace `{id}` (+confirm), and the global sort `{}`.
- Poll loop (`app.js` `pollJob`) hits `GET /api/job/{id}` every ~1s, stops on `done`/`error`, shows a spinner while running, and renders captured output (including errors) without hiding them.
- Editable-vs-informational folder distinction is explicit: input `disabled` + a different note when `suggested_folder.applies` is false (`app.js` `buildFolderCell`), matching the server's `applies` semantics exactly.
- Self-contained: zero external fonts/CDNs/frameworks; spinner is pure CSS; system monospace stack only. Verified `/app.js` and `/styles.css` serve 200 with correct content types.
- Responsive: under 860px the table collapses to stacked labelled cards via `data-label` CSS (`styles.css` media query) while remaining ONE list.

## Weaknesses
- Item-level size is formatted client-side (`fmtSize`) since `/api/reclaim` exposes only `size_bytes` per item; my binary-unit rounding may differ by a digit from the server's `human_readable_size` for borderline values. The header total still uses the server's `total_reclaimable_human` string verbatim, so the headline number is authoritative.
- After an action mutates the library, the table is NOT auto-refetched — the row's badge can become stale until a manual reload. I optimized for keeping the inline job log visible (a refetch would discard it) over auto-refresh; an operator re-runs the page to re-scan. This is a deliberate but real limitation.
- Keyboard focus index is positional (re-clamped on sort/filter), so a heavy re-sort can land the highlight on a different logical row than before; acceptable for a scan-and-act console but not a perfectly stable selection model.
- I could not open a browser, so visual rendering (exact glow, modal centering, color-mix support) is verified only by reading the CSS and the static/in-process checks, not pixels. `color-mix()` is used for pill tints; on a very old engine those would fall back to the base color (still legible).

## Tests run
```
$ node --check webui/static/app.js && echo "NODE_CHECK_OK"
NODE_CHECK_OK
```

```
$ python -c "from fastapi.testclient import TestClient; from webui.server import create_app; c=TestClient(create_app()); r=c.get('/'); print('GET / ->', r.status_code); print('has app.js ref:', './app.js' in r.text); print('has styles.css ref:', './styles.css' in r.text); print('reclaim', c.get('/api/reclaim').status_code)"
GET / -> 200
has app.js ref: True
has styles.css ref: True
reclaim 200
```

Served-content + tag-balance + modal-copy checks (read-only):
```
is mine (has grid table): True
is mine (has modal): True
placeholder gone: True
modal copy present: True
html/head/body/header/table/thead/tbody: all open==close OK
```

Static assets served by the mount:
```
/app.js   -> 200 text/javascript; charset=utf-8
/styles.css -> 200 text/css; charset=utf-8
```
(The `StarletteDeprecationWarning` in the output is pre-existing environment noise, unrelated to these files.)

## Confidence
high

Reasoning for confidence: Every line of the fixed behavior contract is implemented and traceable in `app.js` (four action mappings, ~1s poll loop with spinner, faithful error/output rendering, 409-safe replace gate, copy buttons with offline fallback, editable provider field gated by `applies`, filter chips, global sort). All three required validations pass, plus extra checks confirming MY index.html is served and assets resolve. The one thing I genuinely cannot verify is pixel-level rendering (no browser), but the CSS is plain dark-theme flexbox/grid with conservative fallbacks, so layout risk is low.
