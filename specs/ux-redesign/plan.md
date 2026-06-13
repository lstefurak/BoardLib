# BoardLog UX Redesign — Implementation Plan

Companion to [spec.md](spec.md). Five phases, each independently shippable and
testable, ordered so the worst problems are fixed first. All work happens in
`docs/` (the static client); no backend, Terraform, or CSP changes.

---

## Phase 0 — Baseline & safety net

Before touching layout, capture what "working" means so regressions are obvious.

- Record the current behavior checklist: gate unlock/lock, live fetch happy
  path, 401/403 error paths, CSV load, session prev/next/select, copy button,
  reveal toggles, sessionStorage round-trip on refresh.
- Save a sample CSV under `data/` (one already exists) for offline testing —
  the whole dashboard can be exercised without the backend via the CSV tab.
- Branch: `ux-redesign`.

**Exit:** checklist written into tasks.md and verified against `main`.

## Phase 1 — Fix the broken form & password-manager flow (the screenshot bug)

Highest-value, smallest-risk fix; everything else builds on this structure.

1. **Restructure the connect form** in `index.html`:
   - One fieldset, *Tension account*: username + password, helper text,
     `autocomplete="username"` / `"current-password"`.
   - Remove the access-key input from the form; add the "Room access:
     set ✓ / not set · Change" status line.
   - Move the endpoint URL into `<details class="advanced">`.
   - Single full-width submit at the bottom.
2. **Add the access-key `<dialog>`** (`#keyDialog`): key input, Save/Cancel,
   one-line explanation. JS: open on Fetch-with-no-key and on "Change";
   on Save, stash key in module state; persist to
   `sessionStorage["boardlog:key"]` only after a 2xx (existing behavior).
   `lock()` clears it and resets the status line.
3. **Fix reveal toggles**: stop inserting toggle buttons as flow siblings.
   Wrap each secret input in `.secret-field { position: relative }` in the
   HTML; JS only wires the toggle, absolutely positioned inside the input's
   right edge. (This kills the grid-scrambling at the root.)
4. **Form CSS**: replace the `1.5fr 1fr 1fr auto` grid with a simple stacked
   fieldset layout (username/password side-by-side ≥700px, stacked below).

Touched: `index.html`, `styles.css`, `app.js` (form handler reads the key
from state/dialog instead of `#accessKeyInput`; lock() updated).

**Risk:** `app.js` references `#accessKeyInput` in 4 places — update all and
retest the 403 path. **Exit:** acceptance criteria 1, 3, 9 pass.

## Phase 2 — Identity, header, and step rail

1. Design the inline-SVG logo mark (4×4 hold-dot grid, accent teal) directly
   in `index.html`; reuse via `<svg>` copies (no external file → CSP-safe,
   no extra request).
2. Replace the header block with the compact top bar: mark + "BoardLog"
   wordmark, right side = status pill (`Locked / Connected / n rows`) + Lock
   button with lock glyph.
3. Add the 3-step rail (*Unlock · Load data · Analyze*), driven by existing
   state transitions: gate visible → step 1; app visible, no data → step 2;
   dashboard visible → rail hidden, pill shows row count.
4. Restyle the gate as a centered single card with the logo.

Touched: `index.html`, `styles.css`, small `app.js` hooks in `unlock()`,
`lock()`, `loadRows()` to set a `data-step` attribute on `<body>` (CSS does
the rest — minimal JS).

**Exit:** acceptance criterion 2 passes; lock/unlock still clean.

## Phase 3 — Feedback states

1. Status banner component: icon + message + tone classes; extend
   `setStatus(message, tone)` to set classes instead of inline color (drops
   one inline-style usage too).
2. Fetch busy state: CSS spinner in the submit button, label "Fetching…",
   wired to the existing `exportInFlight` flag.
3. Post-load collapse: connect card shrinks to the one-line summary bar with
   "Load different data" to re-expand.
4. Segmented control styling + ARIA (`tablist`/`tab`/`tabpanel`,
   `aria-selected`, arrow-key handling).

Touched: all three files. **Exit:** acceptance criteria 4, 7 pass.

## Phase 4 — Dashboard polish & CSV drop-zone

1. Session header: chevrons + prominent date heading + jump select.
2. Stat cards with inline-SVG icons and hover elevation.
3. Climbs table: sticky header, zebra rows, send/attempt + benchmark badges
   (rendered in `render()` — keep `escapeHtml` on all user content).
4. CSV tab: drop-zone styling; `dragover`/`drop` handlers feed the existing
   `loadCsvText()` path.
5. Responsive pass at 375/768/1024/1280; focus-visible outlines everywhere.

**Exit:** acceptance criteria 1 (re-verified), 5, 6, 8 pass.

## Phase 5 — Verification & ship

- Run the full Phase 0 checklist on the branch.
- CSP audit: no new script tags, no remote URLs (`grep -n "https://" docs/`
  should only show the Lambda endpoint config and CSP `connect-src`).
- Password-manager test with the browser keeper + one external keeper.
- Cross-browser sanity: Chromium + Firefox (native `<dialog>` is fine in
  both; no polyfill needed).
- PR with before/after screenshots; deploy via Pages on merge to `main`.

---

## Order rationale & risks

- Phase 1 first because the layout bug and keeper-compat change the form's
  DOM contract that later phases style — doing identity work first would be
  styled twice.
- Biggest regression surface is `app.js` ID wiring (every `$("...")` lookup
  fails silently as a TypeError at listener-attach time). Mitigation: change
  IDs only in Phase 1, keep them stable afterward, and smoke-test via the CSV
  path after every phase (no backend needed).
- `sessionStorage` keys (`boardlog:gate`, `boardlog:key`) are part of the
  tab-refresh contract — do not rename.
