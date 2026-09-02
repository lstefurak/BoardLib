# Instagram Reels pipeline: runbook

Pick-up notes for posting Tension board clips to Instagram. Last verified
2026-09-02, when 7 Reels were posted end to end. The research and design
history is in [research.md](research.md); this file is the "how do I run it
again" guide.

## What exists

| Piece | Where | Purpose |
| --- | --- | --- |
| Planner | `tools/instagram_board_publisher.py` | Takeout clips + logbook CSV -> review manifest with generated captions |
| Uploader | `tools/instagram_publish.py` | Posts `approved` manifest records as Reels; dry run by default |
| Staging bucket | `infra/terraform/instagram.tf` -> `boardlog-instagram-staging-528757796356` | Private S3 bucket Meta fetches each clip from via a 1-hour presigned link; objects auto-expire after a day |
| Meta app | developers.facebook.com, app **boardmeow_uploader** (id 1370499055235042) | "Instagram API with Instagram Login" use case; permissions `instagram_business_basic` and `instagram_business_content_publish`, both "Ready for testing" |
| Instagram account | **boardmeow** (Creator account) | Added to the app as an *Instagram Tester* (invite accepted) |
| Tests | `tests/test_instagram_board_publisher.py`, `tests/test_instagram_publish.py` | `PYTHONPATH=src python -m pytest -q --ignore=tests/integration` |

The app stays in **Development mode**. That is enough to post to the tester
account; no App Review, no publishing the app.

## Local data (all gitignored, never committed)

- `videos/` at the repo root: the Google Photos Takeout clips with their
  `*.supplemental-metadata.json` sidecars, plus the logbook CSV used for
  matching (`videos/tension-logbook_8_2026.csv` as of the last run).
- `data/instagram-manifest.jsonl`: the review manifest. **This is the record of
  what has been posted** (`published_media_id` per clip). Back it up before
  experiments; the planner preserves `approved` / `skip` / `published` records
  on re-run, but a deleted file is a deleted file.
- `.env`: the three uploader settings (see Secrets).

## Secrets and where they live

`.env` needs exactly these lines. Values are plain, no quotes, no `<>`:

```
INSTAGRAM_ACCESS_TOKEN=IGAA...        # long-lived token, ~60 days
INSTAGRAM_USER_ID=27982548841409798   # numeric id, NOT the username
INSTAGRAM_STAGING_BUCKET=boardlog-instagram-staging-528757796356
```

- The token generated on 2026-09-02 expires around **2026-11-01**. To renew:
  developers.facebook.com -> the app -> Use cases -> Customize -> "API setup
  with Instagram login" -> section 2 "Generate access tokens" -> **Generate
  token** next to boardmeow. Paste it into `.env`.
- The numeric user id can be re-read any time with the token:
  `GET https://graph.instagram.com/v23.0/me?fields=id,username&access_token=...`
- AWS credentials come from the normal AWS CLI profile (the same one that runs
  Terraform); the uploader uses boto3's default chain.

## The workflow

1. **Fresh logbook.** On the site, Fetch Logs then Export CSV. Put the file in
   `videos/` (or `data/`).
2. **New clips.** Google Takeout -> Photos only, keep the JSON sidecars ->
   unpack the videos and sidecars into `videos/`. Videos already in the manifest
   are recognised by path, so adding to the folder is fine.
3. **Plan.** Always pass the logbook timezone; BoardLib exports naive local
   times and Takeout is UTC.

   ```sh
   python tools/instagram_board_publisher.py videos \
     --logbook videos/tension-logbook_8_2026.csv \
     --output data/instagram-manifest.jsonl \
     --logbook-tz America/New_York
   ```

   It prints a status summary. `ready` = the climb name in your Google Photos
   description matched a log entry that day; `check_climb` = matched by time
   only, the note names the climb it picked; `unmatched` = nothing found, the
   day's climbs are listed under `same_day_climbs`.
4. **Review** `data/instagram-manifest.jsonl`. Per record set
   `"status": "approved"` to post it, or `"skip"`. For a `check_climb` that is
   wrong, either fix `climb_name` and `caption` by hand (your edits survive
   re-runs) or put the climb name in the clip's sidecar `description` and
   re-run the planner. The caption field is exactly what gets posted.
5. **Dry run, then post.**

   ```sh
   python tools/instagram_publish.py
   python tools/instagram_publish.py --execute --continue-on-error
   ```

   About a minute per clip. Each success is written back immediately
   (`published_media_id`, `published_at`), so re-running never double-posts.
   Meta allows 100 API posts per account per day. When driving this from a
   tool with a 10-minute limit, post in batches with `--limit 3`.
6. **Links.** Permalinks are not stored; fetch one with
   `GET https://graph.instagram.com/v23.0/<media id>?fields=permalink&access_token=...`.
   Tension adds tagged videos to the app by hand, on the order of twice a month,
   so the in-app link appears much later.

## Caption format (do not change casually)

Verified against posts the Tension app actually links (`beta_links` in the
board database): the app's own linked posts look like
`"Unsynthesized" v10 @ 40° on the Tension Board. @tensionclimbing #tensionboard #climbing #bouldering`.
The planner generates:

```
"Bring an Axe" V7 @ 30° on the Tension Board.
Sent in 3 tries · April 29, 2026
(harder side)

@tensionclimbing #tensionboard #climbing #bouldering
```

Line 2 comes from the logbook row: `⚡ Flash` (one try, one session),
`Sent in N tries`, `Sent after S sessions (N tries)`, `Repeat send`, or
`Project · N tries so far`, then the day of the month. `(mirror)` is added to
line 1 for mirrored sends. Line 3 is whatever you wrote in the Google Photos
description beyond the climb name, grade and angle. `--tags ""` or
`--board-label "Tension Board 2"` change the fixed parts.

## Troubleshooting

- **"Insufficient developer role"** when clicking *Add account* under
  "Generate access tokens": the Instagram account is not an Instagram Tester
  on the app, or the invite was not accepted. Add it under App roles -> Roles ->
  Add People -> Instagram Tester, then accept at
  https://www.instagram.com/accounts/manage_access/ (Tester Invites tab). The
  invite does **not** show in the Android app.
- **"Invalid OAuth access token - Cannot parse access token"**: the token line
  in `.env` has quotes, angle brackets or whitespace. Paste the bare value.
- **`INSTAGRAM_USER_ID` mismatch**: it must be the numeric id, not `boardmeow`.
- **A clip fails with ERROR/EXPIRED from Meta**: usually the file (too short,
  odd codec, over 1 GB). The record keeps `approved` with `last_error`; fix or
  `skip` it and re-run.
- **Timed out waiting for processing**: safe to re-run; the unpublished
  container simply expires on Meta's side, and the clip is re-staged.
- **Wrong clip got posted**: the API cannot delete media. Delete the Reel in the
  Instagram app, then leave the record as `published` (so it is never
  re-posted) with a note, or mark it `skip`.
- **Planner matched 2 of N**: you forgot `--logbook-tz`.

## State as of 2026-09-02

- 7 Reels posted (Bring an Axe; Words I've Never Said; Baptized in Fire; All
  Arc No Narc; Why Change?; Unfit; Manpris).
- **Unfit was the wrong clip** (`PXL_20260827_124555045~4.mp4`, 1 MB, media id
  17946322536271428, https://www.instagram.com/reel/Dcyq-fPgIPn/). Delete it in
  the app; the manifest record is flagged `wrong_video` and kept as published.
  The real Unfit send still needs a clip.
- 11 clips `ready` and 8 `check_climb` waiting for approval.
- Token renewal due around 2026-11-01.
