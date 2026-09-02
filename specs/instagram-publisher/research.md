# Instagram board-video publisher: research and MVP

Research checked **August 31, 2026**. The recommended first version is a local,
human-reviewed pipeline, not a bot with unrestricted access to the photo library.

## What is feasible

1. Export the historical videos with [Google Takeout](https://takeout.google.com/).
   The included planner reads the video sidecars, uses the capture timestamp and
   keeps an existing Google Photos description.
2. Match each timestamp to the nearest row in a BoardLib logbook CSV. The output
   caption includes the climb, grade, angle, board, and calendar month.
3. Review/edit a JSON Lines manifest. Nothing is uploaded by this MVP.
4. In a second phase, stage only approved videos in short-lived object storage
   and publish them as Reels through Meta's API. Delete staged objects afterward.

Google changed the Photos APIs on March 31, 2025. The Library API's broad
read-only scopes were removed; it now focuses on media created by the app. The
[Picker API](https://developers.google.com/photos/picker/guides/get-started-picker)
is the supported way for a person to select existing library items. Picker is a
reasonable option for new batches, but Takeout is much better for a one-time
backlog of months of videos. See Google's [Photos API updates](https://developers.google.com/photos/support/updates).

ChatGPT does not need to be linked directly to either account. A later optional
caption-enrichment step should send selected frames or a transcript to an OpenAI
API model, return structured caption suggestions, and retain human approval.
Video should be sampled into frames; image-capable models do not accept a video
as one image input. See the official [vision guide](https://platform.openai.com/docs/guides/images-vision)
and [speech-to-text guide](https://platform.openai.com/docs/guides/speech-to-text).

Meta's supported route is the Instagram Platform content-publishing flow. It is
for professional Instagram accounts, requires the relevant permissions and app
review for accounts the app does not own/manage, and creates a media container
before publishing it. Meta must be able to fetch the Reel from a public URL, so
local files require temporary staging. Consult Meta's current
[Instagram Login publishing guide](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing)
before implementing this phase; permissions, limits, and media constraints can
change. Do not automate the consumer Instagram website or store passwords.

## Run the planner

```sh
python tools/instagram_board_publisher.py \
  /path/to/unpacked/Takeout/Google\ Photos \
  --logbook data/tension-logbook.csv \
  --output data/instagram-manifest.jsonl \
  --logbook-tz America/New_York \
  --tolerance-minutes 180
```

Matching prefers a climb named in the clip's Google Photos description (same
day, or the day either side) and marks the record `ready`; the nearest timed log
entry inside the configured window gives `check_climb` (the reviewer confirms
the climb); anything else is `unmatched` with the day's climbs listed under
`same_day_climbs` so the reviewer can name the right one in the description and
re-run. The reviewer sets `approved` or `skip`; the uploader sets `published`.
Re-runs keep approved / skipped / published records, refreshing an approved
caption only when it was left as generated (`caption_generated` records that).

Captions follow the convention Tension's beta-video linking recognises,
verified against posts that the Tension app itself links (`beta_links` in the
board database): `"Climb" V7 @ 30° on the Tension Board.` followed by
`@tensionclimbing #tensionboard #climbing #bouldering`. The second line is the
send story from the logbook: `⚡ Flash`, `Sent in N tries`, `Sent after S
sessions (N tries)`, `Repeat send`, or `Project · N tries so far`, then the day
of the month. `(mirror)` is added for mirrored sends. Whatever the reviewer
wrote in the description beyond the climb name, grade and angle is kept as a
note line. `--tags` and `--board-label` override the fixed parts.

A missing sidecar is called out because file modification time is weaker
evidence than Google capture time. BoardLib exports logbook timestamps as
timezone-naive local times while Takeout timestamps are UTC, so always pass
`--logbook-tz` (an IANA name, or `local`); without it every match is off by
your UTC offset. Entries exported with no time of day (midnight) are never
matched by time.

## Proposed publishing phases

### Phase 1 (included): inventory and deterministic captions

- Takeout stays local.
- Existing descriptions win; missing descriptions get an explicit placeholder.
- Output is restartable, diffable, and easy to edit in bulk.
- No account tokens, AI calls, hosting, or posting are involved.

### Phase 2: optional caption assistance

- Extract a few frames locally with `ffmpeg`; optionally transcribe audio.
- Ask for structured fields (movement, holds, style), not a free-form post.
- Merge AI suggestions with authoritative BoardLib fields.
- Never infer a grade, send, or climb identity when no log matches.

### Phase 3 (included): approved-only uploader

`tools/instagram_publish.py` does exactly this, and nothing more:

- Posts only records with `status: approved`; dry run by default, `--execute`
  to publish, `--limit N` to post a few at a time.
- Stages each clip in the private S3 bucket from `infra/terraform/instagram.tf`
  (public access blocked, encrypted, objects expire after a day as a backstop)
  and hands Meta a presigned link that lives for an hour.
- Creates the Reels container, polls `status_code` until `FINISHED` (or fails
  cleanly on `ERROR` / `EXPIRED` / timeout), publishes, and writes
  `published_media_id`, `published_at` and `container_id` back into the
  manifest after every success, so a re-run can never post the same clip twice.
  Failures are recorded as `last_error` and leave the record `approved`.
- Deletes the staged object whether or not publishing succeeded.
- Reads `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` and
  `INSTAGRAM_STAGING_BUCKET` from the environment or `.env`; the token is never
  a flag, never logged, never written to the manifest.

Setting up the Meta side (once): switch the Instagram account to a
professional account, create a Meta developer app with the "Instagram API with
Instagram Login" product, grant it `instagram_business_basic` and
`instagram_business_content_publish`, generate a long-lived token for your own
account, and note the account's Instagram user id. Meta allows 100 API posts
per account per day.

## Open decisions before Phase 2/3

- Which Instagram professional account owns the posts, and is it already set up
  for Instagram Login/API access?
- Does each clip correspond to the closest log, or can a clip contain several
  climbs? What timezone was used when the historical BoardLib CSV was produced?
- Caption template, hashtags, collaborator/tag/location policy, and whether
  projects should post at all.
- Storage provider and deletion window for temporary public media URLs.
- Whether AI-generated descriptions are useful enough to justify sending frames
  to a third party; deterministic log-based captions may be sufficient.
