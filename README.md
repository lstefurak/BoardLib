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
["board", "angle", "climb_name", "date", "logged_grade", "displayed_grade", "is_benchmark", "tries", "is_mirror", "sessions_count", "tries_total", "is_repeat", "is_ascent", "comment"]
```

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

The static page holds **no secrets** — all GitHub Pages JavaScript is public, so
nothing secret can be hidden there, encrypted or otherwise. Security is enforced
entirely by the Lambda backend.

There are two independent secrets, both typed at runtime and verified
server-side against KMS-encrypted SSM parameters:

- A **gate phrase** (`X-Board-Gate`) that unlocks the page UI. The gate button
  asks the Lambda to verify it, so the door is real, not cosmetic.
- An **access key** (`X-Board-Room-Key`) required for the actual export.

Because they are separate, the gate phrase and access key can be rotated
independently. Configure only the (non-secret) endpoint in `docs/site.config.js`:

```js
window.BOARDLOG_CONFIG = {
  defaultEndpoint: "https://your-lambda-url.lambda-url.us-east-1.on.aws/",
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
ALLOWED_ORIGIN=https://your-user.github.io
BOARDLOG_ACCESS_KEY_PARAM=/boardlog/access-key
BOARDLOG_GATE_PHRASE_PARAM=/boardlog/gate-phrase
BOARDLOG_ALLOWED_BOARDS=tension
BOARDLOG_MAX_SYNC_PAGES=100
```

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

## Bugs 🐞 and Feature Requests 🗒️

Please create an issue in the [issue tracker](https://github.com/lemeryfertitta/BoardLib/issues) to report bugs or request additional features. Contributions are welcome and appreciated.
