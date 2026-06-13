# BoardLog UX Redesign — Specification

**Status:** Draft
**Scope:** `docs/index.html`, `docs/styles.css`, `docs/app.js` (static GitHub Pages client only)
**Out of scope:** Lambda backend, Terraform, security model, data pipeline

---

## 1. Problem statement

The current UI works ("skateboard") but looks and flows poorly:

1. **Broken connect form.** `app.js` appends a "Show" reveal button after every
   password input. Inside the `.source-grid` (a `1.5fr 1fr 1fr auto` grid),
   those buttons change label heights and the `align-items: end` grid scatters
   labels, fields, and Show buttons into visually random positions (see
   screenshot from 2026-06-12).
2. **No visible flow.** The app is really a 3-step funnel — *unlock → load
   data → analyze* — but nothing communicates that. A first-time user lands on
   four credential fields with no grouping, hierarchy, or explanation of which
   secret does what.
3. **Unintuitive button placement.** "Fetch Logbook" sits mid-form under the
   URL field; "Lock" floats disconnected at top-right; tab buttons ("Connect" /
   "CSV") look like actions rather than a source switch.
4. **Plumbing exposed.** The Lambda Function URL is pre-filled config that a
   user almost never edits, yet it's the first and widest field on the form.
5. **No identity.** No logo or mark; "BOARD LOG" eyebrow + giant "Session
   Visualizer" headline eat half the viewport before any content.
6. **Weak feedback.** Status is a small grey text line; fetching gives no
   spinner; errors, successes, and idle states all look nearly identical.

## 2. Goals

- A clear, guided flow a new user can follow without reading the README.
- Logical grouping and placement of inputs and buttons.
- A visual identity (logo mark, consistent type scale, tone colors).
- Proper feedback: loading, success, error, and empty states.
- Responsive layout that doesn't break on mobile.
- **Zero regression** to the security model and CSP.

## 3. Hard constraints

These come from the existing architecture (`ARCHITECTURE.md`) and must hold:

| Constraint | Implication for this redesign |
| --- | --- |
| CSP: `script-src 'self'` — no inline or remote scripts | All JS stays in `app.js`; no CDN frameworks, no icon-font scripts |
| CSP: `img-src 'self' data:` | Logo must be **inline SVG in the HTML**, a `data:` URI, or a file under `docs/` — no remote images |
| CSP: `style-src 'self' 'unsafe-inline'` | Inline `style=""` allowed (used for bar widths); no remote fonts/CSS — keep the system/Inter font stack |
| No build step | Plain HTML/CSS/JS, hand-editable |
| No secrets in the page | Gate phrase, access key, password handled exactly as today (sessionStorage for gate/key, password cleared after each request) |
| Lock must clear secrets from DOM | Preserve the existing `lock()` behavior including clearing input values |
| Existing element IDs are wired in `app.js` | Renaming/restructuring IDs requires synchronized JS updates and a manual retest of every handler |

## 4. Target design

### 4.1 Information architecture

```
┌─ Top bar ─────────────────────────────────────────────────┐
│ [logo mark] BoardLog          [status pill]  [Lock 🔒]    │
└───────────────────────────────────────────────────────────┘
Step rail (only until data loads):  ① Unlock → ② Load data → ③ Analyze

GATE VIEW (step 1)
  Centered card: logo, "The Board Room", knock input + Enter
  (single column, vertically centered, keeps the speakeasy copy)

CONNECT VIEW (step 2) — card with two source tabs
  [ Live export ]  [ Upload CSV ]        ← segmented control, not buttons
  Live export tab:
    ┌ Tension account ───────────────┐
    │ Username        Password [👁]  │   ← the ONLY user+password pair in the
    └────────────────────────────────┘     form → password managers pair them
    Room access: ●●●● set · Change      ← key status line; key itself lives in
    ▸ Advanced — endpoint URL             a separate <dialog>, not this form
    [        Fetch Logbook  →        ]   ← primary, right-aligned/full-width

ACCESS KEY DIALOG (modal <dialog>, opens on first fetch or via "Change")
    "Board Room access key" + one line of explanation
    [ key input 👁 ]  [Cancel] [Save key]
    Saved to sessionStorage (boardlog:key) → sticks for the tab, like today
  Upload CSV tab:
    Drop zone / file picker + [Load CSV]

DASHBOARD VIEW (step 3)
  Session header: ‹ ›  Fri, Jun 12 2026  (date is the headline, not a <select>
  buried in a toolbar; the select remains for jump-to-date)
  Stat cards (5): icon + number + label
  Grade chart  |  Workout log (Copy)
  Climbs table: sticky header, send/attempt + benchmark as colored badges
  "Load different data" link to reopen the connect card
```

### 4.2 Specific changes

**Identity & header**
- Add an inline SVG logo mark: a minimal 4×4 "board holds" dot-grid in the
  accent teal, used in the top bar and gate card. Inline SVG is CSP-safe.
- Replace the oversized `Session Visualizer` h1 with a compact top bar:
  mark + "BoardLog" wordmark left, connection status pill + Lock right.
- Lock button gets a lock glyph and a confirm-free but clearly labeled
  "Lock" action ("locks the room and clears secrets" tooltip via `title`).

**Step indicator**
- A slim 3-step rail under the top bar: *Unlock · Load data · Analyze* with
  the current step highlighted. Hidden once the dashboard renders (status
  pill takes over). Purely presentational — no logic changes to gating.

**Connect form (the main fix)**
- The form contains exactly one credential pair: *Tension account*
  (username + password) in a labeled fieldset with one line of helper text
  ("Your Tension board login — sent to Tension, never stored.").
- **Password-manager compatibility** (explicit requirement):
  - The username and password are the only username/password-shaped fields
    in the `<form>`, with `autocomplete="username"` and
    `autocomplete="current-password"` (already present, but currently
    defeated by the adjacent access-key password field confusing keepers).
  - The access key is **removed from the form entirely** and collected in a
    modal `<dialog>` (see below), so keepers never try to save or fill it.
  - The gate knock input keeps `autocomplete="off"`; if keepers still offer
    to fill it, switch it to `autocomplete="one-time-code"`.
  - Inputs keep stable `name` attributes (`username`, `password`) and live
    in a real `<form>` with a submit button — the pattern keepers detect.
- **Access key dialog.** A native `<dialog>` holds the single access-key
  input + Save/Cancel. It opens automatically on "Fetch Logbook" when no key
  is in sessionStorage, and on demand via a "Change" link in the form's
  "Room access" status line (shows "set ✓" / "not set"). Saving writes the
  key to memory; it is persisted to `sessionStorage["boardlog:key"]` only
  after the backend accepts it (existing behavior, unchanged). Lock clears
  it, as today. Result: the key is entered once per tab and then sticks.
- Move the endpoint URL into a collapsed `<details class="advanced">`
  labeled "Advanced: export endpoint". Pre-filled from `site.config.js`.
- **Reveal toggles become part of the input**: wrap each secret input in a
  `.secret-field` container (`position: relative`), with the eye toggle
  absolutely positioned inside the right edge of the input. They no longer
  participate in the form grid, which fixes the scrambled layout at the root.
- "Fetch Logbook" becomes the single full-width primary button at the bottom
  of the form, with a busy state: label swaps to "Fetching…" + CSS spinner,
  button disabled (logic already exists via `exportInFlight`).
- Tabs become a segmented control ("Live export" / "Upload CSV") visually
  distinct from action buttons; ARIA roles (`tablist/tab/tabpanel`,
  `aria-selected`) wired correctly.

**CSV tab**
- Style the file input as a drop-zone card ("Drop a BoardLib CSV here or
  browse"). Native `<input type=file>` remains the mechanism (label-wrapped),
  drag-and-drop added with `dragover`/`drop` handlers in `app.js`.

**Status & feedback**
- Replace the bare `.status` line with a status banner component: icon +
  message, tone classes `.is-error` (red), `.is-good` (teal), `.is-busy`
  (neutral + spinner). Same `setStatus()` API, extended with the tone.
- After a successful load, the connect card collapses to a one-line summary
  bar ("⚡ 1,234 rows · private export · Load different data") and the
  dashboard becomes the page focus.

**Dashboard polish**
- Session navigation: prev/next chevrons flank a prominent formatted date
  heading; the `<select>` remains beside it for direct jumps.
- Stat cards get small inline-SVG icons (checkmark, hash, repeat, star,
  target) and hover elevation.
- Climbs table: `position: sticky` header, zebra rows, "send"/"attempt"
  rendered as tinted badges, benchmark as a star badge.
- Empty chart state keeps existing copy but gets centered illustration
  treatment (inline SVG).

**Responsive**
- ≤900px: fieldsets stack, stat cards 2-up, table horizontally scrollable
  (already wrapped), top bar wraps to two lines.

### 4.3 Visual language

Keep the existing palette (it's good) but use it more deliberately:

- `--accent` teal: primary buttons, active tab, step highlight, good-tone.
- `--accent-2` amber: chart gradient end, benchmark badges only.
- Type scale: 13/14/16/20/28 px; the 92px display size is retired except on
  the gate card (clamped smaller, ~56px max).
- Radius 8px and 1px `--line` borders stay; cards gain a very soft shadow
  (`0 1px 2px rgb(0 0 0 / .04)`) for hierarchy.
- Focus states: 2px accent outline on all interactive elements (currently
  browser-default only).

## 5. Non-goals

- No framework, bundler, or dependency additions.
- No dark mode (could be a follow-up; palette variables make it cheap).
- No changes to request/response shapes, headers, or storage keys.
- No new analytics, fonts, or third-party assets (CSP forbids them anyway).

## 6. Acceptance criteria

1. Connect form renders with aligned labels/fields at 1280px, 1024px, 768px,
   and 375px widths; reveal toggles sit inside their inputs.
2. A first-time user can identify the 3 steps and which field takes which
   secret without external docs (helper text present, fields grouped).
3. Endpoint URL is hidden by default and editable under "Advanced".
4. Fetch shows a busy state; errors show in the red banner; success collapses
   the connect card and reveals the dashboard.
5. Lock still clears gate, key, password from storage and DOM; gate flow
   unchanged functionally.
6. CSP unchanged; `grep`-able check: no new `<script src>` other than the two
   existing files, no remote URLs in HTML/CSS.
7. Keyboard: tab order follows visual order; tabs operable with arrow keys or
   at minimum Enter/Space; all controls have accessible names; the access-key
   dialog traps focus (native `<dialog>` behavior) and closes on Esc.
8. CSV path works end-to-end including drag-and-drop.
9. **Password-manager check:** with a keeper installed (e.g. Bitwarden /
   browser built-in), the connect form offers to save and later auto-fills
   exactly the Tension username+password — and never prompts for the access
   key or gate phrase. Fetching with a stored key does not reopen the dialog.
