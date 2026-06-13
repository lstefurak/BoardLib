# BoardLog UX Redesign — Task Checklist

Tracks [plan.md](plan.md). Check items off as they land; each phase ends with
the CSV-path smoke test (load `data/` sample CSV → dashboard renders →
prev/next/select/copy work).

## Phase 0 — Baseline

- [ ] Create branch `ux-redesign`
- [ ] Verify baseline behaviors on `main` and record results here:
  - [ ] Gate: wrong knock shows "The door stays shut.", right knock unlocks
  - [ ] Refresh while unlocked stays unlocked (sessionStorage gate)
  - [ ] Lock clears gate + key from sessionStorage and clears key/password inputs
  - [ ] Live fetch happy path returns rows and renders dashboard
  - [ ] 403 (bad key) and 401 (bad Tension creds) show distinct errors
  - [ ] Password input is cleared after every fetch attempt
  - [ ] CSV load works; bad/empty CSV shows error without nuking prior data
  - [ ] Session prev/next clamp at ends; select jumps; Copy button copies log

## Phase 1 — Form fix + password-manager flow

- [ ] `index.html`: rebuild connect form — *Tension account* fieldset only
  (username + password, helper text, correct `autocomplete` tokens)
- [ ] `index.html`: remove access-key input from the form; add "Room access:
  set / not set · Change" status line
- [ ] `index.html`: endpoint URL into collapsed `<details class="advanced">`
- [ ] `index.html`: add `#keyDialog` `<dialog>` (key input, explanation,
  Cancel / Save key)
- [ ] `index.html`: wrap password + key inputs in `.secret-field` containers
  with static toggle buttons (remove JS DOM injection)
- [ ] `app.js`: reveal-toggle code only wires existing buttons (no insert)
- [ ] `app.js`: key handling — read key from state; open dialog when fetch
  has no key; "Change" opens dialog; persist to `boardlog:key` only on 2xx
- [ ] `app.js`: update `lock()` — clear key state + dialog input + status line
- [ ] `app.js`: update all former `#accessKeyInput` references (4 sites)
- [ ] `styles.css`: stacked fieldset layout replaces 4-col `source-grid`;
  `.secret-field` in-input toggle; full-width primary submit
- [ ] Test: form aligned at 375 / 768 / 1024 / 1280 px
- [ ] Test: keeper saves & fills exactly username+password; never prompts
  for access key or knock; stored key skips the dialog on next fetch
- [ ] Test: 403 path reopens/flags the key dialog sensibly
- [ ] CSV-path smoke test

## Phase 2 — Identity, header, step rail

- [ ] Design inline-SVG logo mark (hold-dot grid) + add to gate card and top bar
- [ ] Replace header with compact top bar (mark + wordmark | status pill + Lock)
- [ ] Lock button: lock glyph + `title` explaining it clears secrets
- [ ] Add 3-step rail; drive via `data-step` attribute set in
  `unlock()` / `lock()` / `loadRows()`
- [ ] Restyle gate view as centered card
- [ ] Test: unlock/lock cycle updates rail + pill correctly
- [ ] CSV-path smoke test

## Phase 3 — Feedback states

- [ ] Status banner with tone classes (`is-error` / `is-good` / `is-busy`);
  `setStatus()` sets classes, drop inline color style
- [ ] Submit-button busy state (spinner + "Fetching…") on `exportInFlight`
- [ ] Post-load: connect card collapses to summary bar with
  "Load different data" re-expand
- [ ] Segmented control styling for source tabs + ARIA roles + arrow keys
- [ ] Test: error / success / busy visually distinct; keyboard tab order sane
- [ ] CSV-path smoke test

## Phase 4 — Dashboard polish + CSV drop-zone

- [ ] Session header: chevrons + date heading + jump select
- [ ] Stat cards: inline-SVG icons + hover elevation
- [ ] Climbs table: sticky header, zebra rows, send/attempt + benchmark
  badges (all user content still through `escapeHtml`)
- [ ] Empty-chart state styling
- [ ] CSV drop-zone styling + `dragover`/`drop` handlers → `loadCsvText()`
- [ ] Responsive + `:focus-visible` pass at all four widths
- [ ] CSV-path smoke test (including drag-and-drop)

## Phase 5 — Verify & ship

- [ ] Re-run full Phase 0 checklist on the branch
- [ ] CSP audit: no new scripts/remote URLs in `docs/`
- [ ] Password-manager test: browser keeper + one external (e.g. Bitwarden)
- [ ] Chromium + Firefox sanity pass
- [ ] Before/after screenshots in PR; merge to `main` (Pages deploy)
