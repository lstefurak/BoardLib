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

See [the runbook](specs/instagram-publisher/runbook.md) for the end-to-end workflow, secrets, and troubleshooting, and [the research notes](specs/instagram-publisher/research.md) for the design history.

### Preparing send videos

The local video-preparation CLI makes a timestamped contact sheet so a human or
vision-capable agent can select the first frame where the climber is seated:

```sh
python tools/prepare_send_video.py sheet input.mov \
  --output review/contact-sheet.jpg --interval 0.5
```

Inspect the sheet, then trim at that timestamp and overlay a two-line title box
at the bottom for the first five seconds. Video and audio begin immediately;
there is no separate intro segment. A frame from those five seconds can be
selected as a cover with the title visible. The
four title values below are examples and are supplied at runtime; no personal
details or media are stored in the repository:

```sh
python tools/prepare_send_video.py edit input.mov \
  --output ready/example-send.mp4 --start 3.5 \
  --name "Example Climb" --grade V7 --angle 30 --sent "8/26"
```

The details line uses a heat grade spectrum: **V6 green, V7 lime,
V8 yellow, V9 orange, V10 vivid red**. The climb name stays white on a dark navy
box. The printed grade remains visible as well as its color. Lowercase grades
and `+`/`-` variants use the same grade color; other grades use neutral gray.

The source file is never modified. Output is H.264/AAC MP4, inherited metadata
is removed, and clips without an audio stream are supported. Install the
`ffmpeg` and `ffprobe` executables separately and use the existing Python/Pillow
environment. See the [video tool research and limitations](specs/video-preparation/research.md)
for the evaluated alternatives and rationale.

**No LLM, API key, or paid AI plan is required.** The CLI runs entirely on the
local machine with FFmpeg, ffprobe, and Pillow. The contact sheet is an ordinary
JPEG, so it can optionally be inspected by Gemini, ChatGPT, another
vision-capable model, or a person. The model is not called by this repository
and therefore there is no expected model vendor or tier. If using an assistant,
upload only the contact sheet—not the private source video—and ask it to return
the earliest timestamp at which the climber is seated; confirm that timestamp
yourself before passing it to `edit`.

#### Readiness checklist

This is a local command-line tool, not a hosted service, so there is nothing to
deploy. Before processing a real clip:

1. Install Python 3.8 or newer and create/activate a virtual environment.
2. Install the repository dependencies with `python -m pip install -r requirements.txt`.
3. Install FFmpeg using the package manager for the workstation (`brew install
   ffmpeg` on macOS, `winget install Gyan.FFmpeg` on Windows, or `sudo apt
   install ffmpeg` on Ubuntu/Debian).
4. Run `python tools/prepare_send_video.py check`. Do not continue until it
   reports versions for FFmpeg, ffprobe, and Pillow.
5. Run `sheet`, inspect the JPEG, and note the earliest clearly seated timestamp.
6. Run `edit` with that timestamp and the four title values.
7. Watch the complete output once before sharing it; confirm the cut, title,
   orientation, audio, and absence of private material.

The tool has been tested on a representative rotated phone clip on Windows.
Review each result for title placement, orientation, timing, audio, and privacy
before sharing. `--title-seconds` changes the five-second overlay duration.

## Bugs 🐞 and Feature Requests 🗒️

Please create an issue in the [issue tracker](https://github.com/lemeryfertitta/BoardLib/issues) to report bugs or request additional features. Contributions are welcome and appreciated.
