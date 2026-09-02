# BoardLib 🧗‍♀️

Utilities for interacting with climbing board APIs.

## Installation 🦺

`python3 -m pip install boardlib`

## Usage ⌨️

Use `boardlib --help` for a full list of supported board names and feature flags.

### Databases 💾

To download the climb database for a given board:

`boardlib database <board_name> <database_path> --username <board_username>`

This command will first download a [sqlite](https://www.sqlite.org/index.html) database file to the given path. After downloading, the database will then use the sync API to synchronize it with the latest available data. The database will only contain the "shared," public data. User data is not synchronized. If a database already exists as `database_path`, the command will skip the download step and only perform the synchronization.

NOTE: The Moonboard is not currently supported for the database command. Contributions are welcome.

#### Supported Boards 🛹

All [Aurora Climbing](https://auroraclimbing.com/) based boards (Kilter, Tension, etc.).

### Logbooks 📚

First, use the `database` command to download the SQLite database file for the board of interest. The database is not required for any version of the Moonboard. Then download your logbook entries for a given board:

`boardlib logbook <board_name> --username=<board_username> --output=<output_file_name> --database-path=<database_path>`

This outputs a CSV file with the following fields:

```json
["board", "angle", "climb_name", "date", "logged_grade", "displayed_grade", "is_benchmark", "tries", "is_mirror", "sessions_count", "tries_total", "is_repeat", "is_ascent", "comment", "climb_uuid", "ascensionist_count", "quality_average"]
```

The last three fields come from the board's shared `climb_stats`/`climbs` data:
`climb_uuid` is the board's stable climb identifier, and `ascensionist_count` /
`quality_average` are community stats (total sends and average star rating) for
the climb at the logged angle. They are populated for Aurora boards only and
left empty for the Moonboard.

#### Supported Boards 🛹

Currently all [Aurora Climbing](https://auroraclimbing.com/) based boards (Kilter, Tension, etc.) and the [Moonboard](https://moonboard.com/). The Moonboard web API currently appears to be broken for some iterations of the board, including 2016 and 2024.

### Logbook Visualizer

After exporting a logbook CSV, generate a standalone HTML session report with:

```sh
python tools/logbook_visualizer.py data/tension-logbook.csv -o data/tension-logbook-report.html
```

The report includes:

- A session date picker.
- Counts of climbs by logged grade for the selected day.
- Session totals for ascents, unique climbs, tries, benchmarks, and repeats.
- A copyable workout log summary for a training journal.
- Per-session climb details and an all-sessions summary table.

To preview the generated report locally:

```sh
python -m http.server 8765 --bind 127.0.0.1 --directory data
```

Then open `http://127.0.0.1:8765/tension-logbook-report.html` in a browser.

### GitHub Pages App

The `docs/` folder contains a static GitHub Pages app called "The Board Room." It can load a local BoardLib CSV in the browser, or call a private AWS Lambda Function URL that returns JSON logbook rows.

The dashboard has two views:

- **Sessions** — the original per-day report: stat cards, grade chart, workout
  log, and the day's climb list.
- **Climbs** — per-climb send history: one row per climb + angle (mirrors
  listed separately) with sends, tries to first send / total tries, sessions,
  first-send and last-climbed dates, plus flash/repeat/benchmark/project
  badges and community stats (community sends, average star rating) when the data
  includes them. Rows expand to show the full attempt history, and the view
  can be searched, filtered (sent / projects / repeated / flashed /
  benchmarks, plus a multi-select angle filter), and sorted by any column
  from its header.

The static page holds **no secrets** — all GitHub Pages JavaScript is public, so
nothing secret can be hidden there, encrypted or otherwise. Security is enforced
entirely by the Lambda backend.

From the user's side the whole login is one **gate phrase** (the "knock"). The
page sends it as `X-Board-Gate`; the Lambda verifies it against a KMS-encrypted
SSM parameter and answers with a short-lived **session token**, and every export
presents that token as `X-Board-Session`. The token lives in `sessionStorage`
for the tab; the phrase itself is never stored.

Under the hood there is still a second, independent secret — the **access key**
(`X-Board-Room-Key`) — which scripts can send alongside the gate phrase, and
which the Lambda combines with the gate phrase to sign session tokens. The page
never sees it. Rotating either secret revokes every outstanding session.
Configure only the (non-secret) endpoint in `docs/site.config.js`:

```js
window.BOARDLOG_CONFIG = {
  defaultEndpoint: "https://your-url-id.lambda-url.<region>.on.aws/",
};
```

To preview the Pages app locally:

```sh
python -m http.server 8766 --bind 127.0.0.1 --directory docs
```

Then open `http://127.0.0.1:8766/`.

### AWS Lambda JSON Backend

The `backend/` folder contains a Lambda Function URL handler for the GitHub Pages app. It accepts Tension credentials for one request, downloads/syncs the Tension database into Lambda's temporary cache, fetches the logbook, returns JSON rows, and does not store the password.

Set the Lambda handler to:

```text
backend.boardlog_lambda.handler.lambda_handler
```

Recommended environment variables:

```text
BOARDLOG_ACCESS_KEY_PARAM=/boardlog/access-key
BOARDLOG_GATE_PHRASE_PARAM=/boardlog/gate-phrase
BOARDLOG_ALLOWED_BOARDS=tension
BOARDLOG_MAX_SYNC_PAGES=100
BOARDLOG_SESSION_TTL_SECONDS=43200
```

(The allowed CORS origin is set on the Function URL via the `allowed_origin`
terraform variable, not as a Lambda env var.)

The access key and gate phrase live in SSM SecureString parameters (KMS-encrypted),
created out-of-band so their plaintext never enters terraform state. See
`infra/terraform/README.md` for creation and independent rotation.

Request body:

```json
{
  "board": "tension",
  "username": "your_tension_username",
  "password": "your_tension_password"
}
```

Response body:

```json
{
  "board": "tension",
  "row_count": 270,
  "rows": []
}
```

Avoid request body logging in Lambda or any proxy in front of it.

### Images 📸

First, use the `database` command to download the SQLite database file for the board of interest. Then download the images for a given board:

`boardlib images <board_name> <database_file> <output_directory>`

This will fetch all of the images for the given board and place them in `output_directory`.

#### Supported Boards 🛹

All [Aurora Climbing](https://auroraclimbing.com/) based boards (Kilter, Tension, etc.).

### Instagram video planning

An experimental, local-only tool can inventory an unpacked Google Photos
Takeout, match video timestamps to a BoardLib logbook CSV, and generate a
human-reviewable caption manifest. It does not upload or post media:

```sh
python tools/instagram_board_publisher.py /path/to/Takeout/Google\ Photos \
  --logbook data/tension-logbook.csv --output data/instagram-manifest.jsonl \
  --logbook-tz America/New_York
```

Pass the timezone your logbook was recorded in: BoardLib exports naive local
times, Takeout timestamps are UTC.

Captions are generated in the form Tension's beta-video linking recognises,
with the send story taken from the logbook (a lightning bolt for a flash, tries
for a one-session send, sessions when it took longer, "Project" for attempts),
the day of the month, anything extra you wrote in the Google Photos description,
and the `@tensionclimbing #tensionboard #climbing #bouldering` tags:

```text
"Bring an Axe" V7 @ 30° on the Tension Board.
Sent in 3 tries · April 29, 2026
(harder side)

@tensionclimbing #tensionboard #climbing #bouldering
```

Each clip gets a status: `ready` (climb confirmed by your description),
`check_climb` (matched by time only, confirm the climb), or `unmatched` (name
the climb in the description and re-run). Set `"status": "approved"` on the
clips to post, or `skip`. Re-running the planner keeps approved, skipped and
published records; an approved caption you have not edited is refreshed. Then
publish them as Reels (dry run by default; `--execute` posts for real):

```sh
python tools/instagram_publish.py --manifest data/instagram-manifest.jsonl
python tools/instagram_publish.py --manifest data/instagram-manifest.jsonl --execute
```

Publishing needs `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` and
`INSTAGRAM_STAGING_BUCKET` in the environment or `.env` (the bucket comes from
`terraform output instagram_staging_bucket`). Each clip is staged in that
private bucket behind a short-lived link, published, recorded back into the
manifest with its media id so it can never be posted twice, and removed.

See [the research, privacy notes, and proposed publishing phases](specs/instagram-publisher/research.md).

## Bugs 🐞 and Feature Requests 🗒️

Please create an issue in the [issue tracker](https://github.com/lemeryfertitta/BoardLib/issues) to report bugs or request additional features. Contributions are welcome and appreciated.
