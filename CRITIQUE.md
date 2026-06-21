# Candidate B Self-Critique

## Approach taken
Candidate B is a **responsive card grid**: each reclaim item is a tile in a CSS-grid
that auto-fits `minmax(340px, 1fr)` columns and reflows to 1-up under 420px. The
visual is dark and "futuristic-but-functional": a near-black background with two
faint radial accent glows (teal + blue), a sticky frosted-glass header, and a
**big gradient hero stat** showing the reclaimable total (e.g. `42.7 GB`) with the
item count beneath it. Each card opens with a **poster-placeholder** block — a
state-tinted gradient banner with a huge translucent initial letter (derived from
the item id's slug), NO external image — and a **color-coded badge pill** floating
on it. The four states read at a glance by hue: UNPREPPED = amber, LOCAL·NOT-PUSHED
= blue, PUSHED·NOT-ARCHIVED = violet, RESTORED·REPLACE-AGAIN = orange (the same hue
drives the chip dot, the badge, and the poster gradient). The card body shows the id
(+a dashed `GUESS` tag when `guessed`), a size pill in accent teal, a monospace path,
the read-only suggested command in a dark code box with a `⧉` Copy button, and the
suggested folder as an editable monospace input with its own Copy button (disabled +
annotated "Existing folder — never renamed" when `applies:false`; enabled + teal
"edit the {provider-…} id" note when `applies:true`). One large (≥44px) action button
sits at the bottom — teal gradient for prep/push, dark-red for the destructive
replace. The replace confirm is a **shared centered modal** with a red `!` icon, the
exact line "This deletes the original after verifying the cloud upload.", the target
id+path, and Cancel / "Delete original" buttons; it pops with a scale animation over a
blurred backdrop and focuses Cancel by default. Job results render inline in a panel
under the acting card (spinner + "Running" while polling, then ✓ Done / ✕ Error with
the captured stdout in a `<pre>`).

## Design decisions and tradeoffs
- **One presentation table (`BADGE_META`) drives everything.** badge → label, CSS key,
  action name, button verb, and `confirm` flag all live in a single object keyed by the
  four underscore strings (`app.js:25`). The chip order, poster hue, badge, action body,
  and modal gating all read from it, so the four states can never drift out of sync.
  Alternative was per-state branching scattered through render; rejected as error-prone.
- **`<template>` clone + DOM APIs, zero `innerHTML`.** The card is an inert
  `<template id="card-tpl">` cloned per item; every dynamic value is assigned via
  `textContent` / `input.value`. This is both XSS-safe (captured `cmd_*` stdout goes
  through `<pre>.textContent`, `app.js` `renderJob`) and keeps the JS readable.
  Tradeoff: a bit more boilerplate than a template-string builder, but it satisfies the
  hard "never innerHTML for data" rule with no escaping logic to get wrong.
- **Poll re-enables the button on BOTH done and error**, and the action button is
  disabled for the duration of the poll. I deliberately re-render whatever status/output
  the job reports without special-casing — including the known server quirk where a
  successful `sort` is marked `error` (the task said render it faithfully, do not
  special-case). Tradeoff: a successful sort shows "✕ Error" with its real output; that
  is the contract's intent until the server bug is fixed separately.
- **Modal uses BOTH `hidden` and a `.show` class.** `hidden` keeps it inert/announced-as-
  hidden for assistive tech and as a no-CSS fallback; `.show` drives the flex layout +
  animation. Esc, backdrop click, and Cancel all route through one `closeModal()`, and
  `replace` POSTs only from the confirm handler — there is no code path that sends
  `replace` without `confirm:true`.
- **Clipboard fallback.** `copyText` uses `navigator.clipboard` only when
  `window.isSecureContext`, else (and on any rejection) falls back to a hidden-textarea
  `execCommand('copy')` — correct for the non-secure `http://127.0.0.1` console.

## Strengths
- Single source of truth for state→behavior: `BADGE_META` / `BADGE_ORDER` (`app.js:25`,
  `app.js:62`) — adding/renaming a state is a one-object edit.
- Strictly XSS-safe rendering: job output via `<pre>.textContent` in `renderJob`
  (`app.js`), no `innerHTML` anywhere in the file.
- Correct, contract-exact action bodies in `bodyForAction` (`app.js`): `prep`
  `{id, filepath}`, `push` `{id}`, `replace` `{id, confirm:true}`, `sort` `{}`.
- `applies` distinction is honored in the UI: editable+annotated input when true,
  disabled+"never renamed" note when false (`buildCard`, the folder block).
- Genuinely responsive + touch-friendly: auto-fit grid, 1-up under 420px, ≥44px action
  buttons, 40px copy/sort targets, hover elevation — the "polished app" feel the
  card-grid approach is meant to deliver.
- Self-contained: system font stack, CSS gradients for the poster placeholder, no CDN /
  font / framework / build step. Works fully offline on localhost.

## Weaknesses
- **No id-edit write-back for `guessed` UNPREPPED items.** I surface `guessed` with a
  `GUESS` tag but the prep action posts the server-provided `item.id` as-is; I do not let
  the user correct the guessed id before prepping (the contract only mandated an editable
  *provider-id* field on the folder, which I do provide). A future enhancement could make
  the id editable and feed the edited value into the prep body.
- **Editable provider-id field is display/copy-only.** Per the contract the editable
  folder field is for the user to copy a corrected folder name; it is not wired into any
  POST (no action consumes a folder in the fixed contract). It influences only the Copy
  button output. That is intentional but worth flagging.
- **`Content-Type: application/json` on POST.** Sent for correctness; FastAPI's
  `Body(default=None)` accepts it. If a candidate environment proxied differently this is
  the standard JSON path; verified `/api/reclaim` + `GET /` via TestClient but could not
  exercise a live POST/poll without triggering a real action (explicitly forbidden).
- **Filter "no matches" empty-state** is created lazily and toggled by `display`; it is
  not announced via aria-live (the main status line is). Minor a11y gap.
- I could not open a browser, so layout/hover/modal animation are reasoned about from the
  CSS, not visually confirmed.

## Tests run

`node --check webui/static/app.js`:
```
NODE_CHECK_OK
```
(node v24.16.0)

In-process TestClient serve check (read-only, no action triggered):
```
GET / -> 200
has app.js ref: True
has styles.css ref: True
reclaim 200
```

index.html well-formedness / tag-balance (custom HTMLParser, void-aware):
```
unclosed stack: []
errors: []
refs ./app.js: True | ./styles.css: True
OK
```

Static dir:
```
app.js     17707 bytes
index.html  3411 bytes
styles.css 15382 bytes
```

## Confidence
high

Reasoning for confidence: All three required validations pass cleanly — `node --check`
is green, the TestClient confirms `GET /` serves my index.html (both relative refs
present) and `/api/reclaim` is 200, and the HTML parses with a balanced tag stack. I
verified every clause of the fixed behavior contract against the actual code (the four
action mappings, the 1s poll loop, the 409-safe replace-confirm gate, both copy buttons
with execCommand fallback, the client-side chip filtering, and textContent-only output).
The main thing I could not do is exercise a live action/poll in a browser (forbidden, and
it would hit real `cmd_*`), so the runtime poll/modal flow is verified by code review and
the static checks rather than an end-to-end click.
