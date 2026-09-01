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

Each record starts with `status: needs_review`. Matching prefers a climb named
in the clip's Google Photos description (same day, or the day either side),
then the nearest timed log entry inside the configured window; anything else is
left unmatched with the day's climbs listed under `same_day_climbs` so the
reviewer can name the right one in the description and re-run. A missing
sidecar is called out because file modification time is weaker evidence than
Google capture time. BoardLib exports logbook timestamps as timezone-naive
local times while Takeout timestamps are UTC, so always pass `--logbook-tz`
(an IANA name, or `local`); without it every match is off by your UTC offset.
Entries exported with no time of day (midnight) are never matched by time.

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

### Phase 3: approved-only uploader

- Require `status: approved`, an explicit account ID, and a dry-run default.
- Upload the video to a private bucket and issue a short-lived fetch URL.
- Create the Reel container, poll its status, publish it, record the returned
  media ID, and remove the staged object.
- Store Meta/OpenAI credentials in environment variables or an OS keychain;
  never put tokens or signed URLs in the manifest or Git.
- Add idempotency using a stable hash so reruns cannot duplicate posts, plus a
  rate limiter and an operator-visible failure log.

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
