# Reviewed sends: preparation, review, and Instagram upload

The workflow uses local originals, a JSON Lines preparation manifest, and a
separate publication journal. Generated videos, covers, review decisions,
manifests, and credentials stay local. Install the repository dependencies and
FFmpeg/ffprobe, then run `python tools/prepare_send_video.py check`.

## 1. Confirm the climb and the outcome

Generate the source manifest with `tools/instagram_board_publisher.py` as
described in the Instagram runbook. A description match identifies the climb;
a logbook ascent does not establish that this recording contains a send.
Time-only matches need explicit label confirmation.

Create the local outcome review page:

```sh
python tools/review_send_videos.py review videos \
  --manifest data/instagram-manifest.jsonl \
  --output tmp/video-outcome-review.html --no-sheets
```

Open the generated HTML locally. Watch the complete recording, classify it as
send, fall/try, or uncertain, confirm the name/grade/angle/mirror label, and
choose a start before any climbing movement. Keep a standing or crouched start
when the recording has no seated frame. Already-seated recordings can start at
zero. Use a timestamped contact sheet when finer start inspection is useful:

```sh
python tools/prepare_send_video.py sheet videos/example.mp4 \
  --output tmp/example-sheet.jpg --interval 0.25
```

Click **Download review JSON** and save the edited download as
`tmp/reviewed-outcomes.json`, then apply the decisions from that file:

```sh
python tools/review_send_videos.py apply tmp/reviewed-outcomes.json \
  --manifest data/instagram-manifest.jsonl --approve-sends --skip-falls
```

The handoff validates numeric bounds and completed review timestamps before
writing. Confirming a label clears its pending-confirmation flag. Existing
published records keep their publication status and IDs. Outcome review alone
does not approve an unresolved label. Uncertain clips remain excluded.

Relative media paths are resolved against the JSON file containing them. A
minimal reviewed input record can look like this; the actual review tool also
stores a source fingerprint so replaced files cannot inherit old decisions:

```json
{
  "status": "approved",
  "video_path": "../videos/example.mp4",
  "taken_at": "2026-01-01T09:00:00-05:00",
  "climb_name": "Example Climb",
  "caption": "\"Example Climb\" V7 @ 30° (mirror) on the Tension Board.",
  "label_confirmed": true,
  "video_outcome": "send",
  "video_reviewed_at": "2026-01-01T15:00:00+00:00",
  "start_seconds": 3.5,
  "grade": "V7",
  "angle": "30",
  "display_date": "1/1"
}
```

Keep capture and review timestamps timezone-aware. Grade and angle can be
derived from the caption; display date can be derived from the capture date.
The batch preserves the original ending. `end_seconds` records review context
and is not an instruction to remove the finish.

## 2. Prepare the approved sends

```sh
python tools/prepare_send_batch.py prepare \
  --manifest data/instagram-manifest.jsonl --output-dir outputs/ready-sends
```

Only approved, reviewed sends with confirmed labels are processed, oldest
first. Each output keeps the full climbing sequence and original ending,
removes audio, and displays the title for the first five seconds. The default
box sits inside the top of the phone preview area. Names use up to two rows;
the grade/angle/date row is larger. Wrapped mirrored names end in `(mir)`, or
`... (mir)` after shortening. Capture metadata is removed from the derivative.

Preparation checks original/output hashes, duration, display dimensions, no
audio, and a full video decode. It creates a cover and a small phone-grid
preview, then marks that record prepared. It never replaces the original or
an unrelated existing export. Re-running skips an output only when its hashes
and preparation settings still match; changed inputs or outputs stop the run.
Use a fresh output directory and reviewed manifest for a new set of edits.

`--limit` prepares a small trial batch. The title duration, position, and margin
options match the single-video editor. The ordered publisher below requires
the approved top-position, silent layout.

Review the exports in the local gallery:

```sh
python tools/serve_video_review.py --output-dir outputs/ready-sends --port 8771
```

Open `http://127.0.0.1:8771/review.html`. Watch each result and inspect its phone
preview. The server binds only to loopback, supports video seeking, and serves
the gallery and media assets within the specified directory.

## 3. Publish with the token uploader

Configure `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID`, and
`INSTAGRAM_STAGING_BUCKET` as described in the
[Instagram runbook](../instagram-publisher/runbook.md). The staging bucket
remains private; Meta downloads through a temporary presigned URL.

Inspect the queue without publishing:

```sh
python tools/publish_ready_sends.py \
  --manifest data/instagram-manifest.jsonl \
  --ledger data/instagram-publications.jsonl
```

Publish the eligible sends:

```sh
python tools/publish_ready_sends.py \
  --manifest data/instagram-manifest.jsonl \
  --ledger data/instagram-publications.jsonl \
  --expected-username your_account --execute
```

The account is verified before any upload. The optional username guard catches
an unintended account configuration. Sends upload in capture-date order and
appear in both Reels and the main profile grid. The cover is selected while
the title is visible. Source/output hashes and current review decisions are
checked before publishing.

The uploader writes only the publication journal; it never rewrites the
preparation manifest. Preparation and upload can run together using that
separate journal. The oldest eligible clip blocks newer clips until its
preparation is complete (`--wait-seconds` controls the wait). Falls, uncertain
outcomes, and unapproved clips without pending publication attempts are skipped.

## 4. Resume and refresh the gallery

Re-run the same command with the same manifest and journal. Published IDs are
retained, processed containers are reused, and staging cleanup is retried.
Unresolved API requests stop for reconciliation instead of creating a duplicate
post. A changed or withdrawn review cannot publish an old processed container.
If upload is interrupted before the staging SDK returns its key, the bucket's
existing lifecycle policy provides cleanup.

Keep the journal after successful publication. To show publication links in
the gallery:

```sh
python tools/prepare_send_batch.py review \
  --manifest data/instagram-manifest.jsonl --output-dir outputs/ready-sends \
  --publication-ledger data/instagram-publications.jsonl
```

Deleting an obsolete Instagram Reel is a separate action in Instagram. These
tools do not delete live posts or original recordings.
