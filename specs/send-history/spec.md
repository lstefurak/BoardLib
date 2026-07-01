# Send History Analytics — Specification

**Status:** Implemented (v1)
**Scope:** `docs/` (Climbs view), `src/boardlib` (community-stats columns)

## 1. Problem

The Board Room dashboard was session-centric: pick a date, see that day's
stats. It could not answer per-climb questions — *which climbs have I sent, at
which angle, in how many tries, and have I repeated them?*

## 2. v1 (this change)

### Climbs view (docs app)

A second dashboard view, toggled by a Sessions/Climbs tab switcher:

- One row per **climb + mirror + angle** — the same grouping BoardLib uses for
  `sessions_count`/`tries_total`, because board grades are angle-specific.
- Columns: climb (with badges), grade, angle, sends, tries (to first send /
  total), sessions, first send, last climbed, community (sends · ★quality).
- Badges: `project` (never sent), `flash` (first logged entry is a 1-try
  ascent), `repeated` (≥2 sends), `benchmark`.
- Search by name, filter chips (all / sent / projects / repeated / flashed /
  benchmarks), sort (last climbed / grade / tries / sends / name / angle).
- Rows expand (click or Enter/Space) to the full attempt history: date,
  send/attempt, tries, grade, comment.
- Works for both data sources (CSV upload and live Lambda export). CSVs that
  predate the community columns simply hide the Community column.
- Same constraints as the rest of the app: no frameworks, no new scripts, no
  CSP changes, all board/user-authored strings escaped before `innerHTML`.

### Community stats columns (library)

The shared `climb_stats` table (already downloaded/synced for grades) also
carries community data per `(climb_uuid, angle)`. The logbook now includes:

- `climb_uuid` — the board's stable climb identifier.
- `ascensionist_count` — how many people have sent the climb at that angle.
- `quality_average` — average star rating at that angle.

These flow through `boardlib logbook` CSVs (appended columns), the Lambda JSON
export (automatic — it serializes the whole DataFrame), and the web app.
Moonboard logbooks leave them empty.

## 3. Aurora API findings (for a possible v2)

Investigated while building v1:

| Source | What it has | Cost |
| --- | --- | --- |
| `climb_stats` shared table (synced sqlite) | `ascensionist_count`, `difficulty_average`, `quality_average`, `benchmark_difficulty`, `fa_username`, `fa_at` per (climb, angle) | Free — already synced by the `database` command and the Lambda |
| `climbs` shared table | `setter_username`, `description` (only `name` is read today) | Free — same sync |
| `GET /climbs/{uuid}/stats?angle=` (`boardlib.api.aurora.get_climb_stats`) | Live per-climb stats (grade/quality distributions) | One authenticated HTTP call **per climb** — unsuitable for the every-load path |
| `GET /users/{id}/followers`, `/followees`, `/notifications` | Social graph, followee ascent feed | Per-call; niche |

## 4. v2 sketch (not built)

The current Lambda re-syncs and rebuilds the logbook on every export. A v2
could publish the dashboard behind a small backend with storage (e.g.
Cloudflare Workers + KV/D1, or extending the existing Lambda with S3/DynamoDB):

- Cache the computed logbook per user, refreshed on demand or on a schedule,
  so the page loads instantly without hitting the Aurora API each time.
- Enrich cached climbs with `get_climb_stats` responses (grade/quality
  distributions) fetched lazily and cached by `(climb_uuid, angle)` — the
  per-climb call cost is then paid once, not per page load.
- Track history over time (e.g. grade pyramid progression), which requires
  persistence the current stateless export cannot provide.

Key design input from v1: `climb_uuid` is now exported everywhere, so any v2
cache can key on `(climb_uuid, angle)` instead of names.
