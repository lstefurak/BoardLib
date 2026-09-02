#!/usr/bin/env python3
"""Publish approved clips from the review manifest to Instagram as Reels.

Reads the JSON Lines manifest written by ``instagram_board_publisher.py`` and
posts only records whose ``status`` is ``approved``. Without ``--execute`` it is
a dry run: it validates each approved clip and prints what it would post, and
touches nothing.

With ``--execute``, for each approved clip it:

1. uploads the video to the private S3 staging bucket and mints a short-lived
   presigned URL (Meta must download the file from a URL it can reach);
2. creates a Reels media container with the caption;
3. polls the container until Meta has finished processing the video;
4. publishes it and writes the returned media id back into the manifest, so a
   re-run can never post the same clip twice;
5. deletes the staged object, whether or not publishing succeeded.

Configuration comes from the environment (a ``.env`` at the repo root is read
if present; real environment variables win):

    INSTAGRAM_ACCESS_TOKEN   long-lived token for the professional account
    INSTAGRAM_USER_ID        the account's Instagram user id
    INSTAGRAM_STAGING_BUCKET S3 bucket from terraform output instagram_staging_bucket

The token is never accepted on the command line, never logged, and never
written to the manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import requests

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "data" / "instagram-manifest.jsonl"
GRAPH_BASE = "https://graph.instagram.com"
DEFAULT_API_VERSION = "v23.0"
MAX_CAPTION_CHARS = 2200
MAX_VIDEO_BYTES = 1024 ** 3  # Meta's Reels limit
VIDEO_SUFFIXES = {".mp4", ".mov"}


class PublishError(Exception):
    """A clip could not be published; the message is safe to show and store."""


def load_dotenv(path: pathlib.Path) -> None:
    """Minimal .env reader (KEY=value lines); existing environment always wins."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- Meta -------------------------------------------------------------------


class MetaClient:
    """The three Instagram content-publishing calls the uploader needs."""

    def __init__(
        self,
        user_id: str,
        access_token: str,
        api_version: str = DEFAULT_API_VERSION,
        session: Optional[Any] = None,
        base_url: str = GRAPH_BASE,
    ):
        self.user_id = user_id
        self._token = access_token
        self.api_version = api_version
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")

    def _call(self, method: str, path: str, **fields: Any) -> dict[str, Any]:
        url = f"{self.base_url}/{self.api_version}/{path}"
        fields["access_token"] = self._token
        # The token travels in the form body (POST) or query (GET) as the API
        # requires; it is never interpolated into the path or logged.
        if method == "POST":
            response = self.session.request(method, url, data=fields, timeout=120)
        else:
            response = self.session.request(method, url, params=fields, timeout=120)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400 or "error" in payload:
            error = payload.get("error") or {}
            detail = error.get("error_user_msg") or error.get("message") or f"HTTP {response.status_code}"
            code = error.get("code")
            raise PublishError(f"Meta API error{f' {code}' if code else ''}: {detail}")
        return payload

    def create_reel_container(self, video_url: str, caption: str, share_to_feed: bool) -> str:
        payload = self._call(
            "POST",
            f"{self.user_id}/media",
            media_type="REELS",
            video_url=video_url,
            caption=caption,
            share_to_feed="true" if share_to_feed else "false",
        )
        container_id = str(payload.get("id") or "")
        if not container_id:
            raise PublishError("Meta did not return a media container id")
        return container_id

    def container_status(self, container_id: str) -> tuple[str, str]:
        payload = self._call("GET", container_id, fields="status_code,status")
        return str(payload.get("status_code") or ""), str(payload.get("status") or "")

    def publish(self, container_id: str) -> str:
        payload = self._call("POST", f"{self.user_id}/media_publish", creation_id=container_id)
        media_id = str(payload.get("id") or "")
        if not media_id:
            raise PublishError("Meta did not return a media id after publishing")
        return media_id


def wait_for_container(
    meta: MetaClient,
    container_id: str,
    poll_interval: float,
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    deadline = clock() + timeout
    while True:
        code, status = meta.container_status(container_id)
        if code == "FINISHED":
            return
        if code in {"ERROR", "EXPIRED"}:
            raise PublishError(f"Meta could not process the video ({code}): {status or 'no detail'}")
        if clock() >= deadline:
            raise PublishError(f"timed out after {int(timeout)}s waiting for Meta to process the video (last status: {code or 'unknown'})")
        sleep(poll_interval)


# --- S3 staging --------------------------------------------------------------


class S3Stager:
    """Stage a clip where Meta can fetch it, then remove it."""

    def __init__(self, bucket: str, presign_seconds: int, client: Optional[Any] = None):
        self.bucket = bucket
        self.presign_seconds = presign_seconds
        if client is None:
            import boto3  # only needed for real runs; tests inject a fake

            client = boto3.client("s3")
        self.client = client

    def stage(self, video: pathlib.Path) -> tuple[str, str]:
        key = f"instagram/{uuid.uuid4().hex}/{video.name}"
        self.client.upload_file(str(video), self.bucket, key, ExtraArgs={"ContentType": "video/mp4"})
        url = self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.presign_seconds
        )
        return key, url

    def discard(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


# --- Manifest ----------------------------------------------------------------


def read_manifest(path: pathlib.Path) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise PublishError(f"{path}:{line_number}: not valid JSON ({error.msg})") from error
    return records


def write_manifest(path: pathlib.Path, records: list[dict[str, Any]]) -> None:
    """Rewrite the manifest atomically so a crash never leaves it half-written."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def validate(record: dict[str, Any]) -> list[str]:
    """Reasons this approved record cannot be posted (empty list = ready)."""
    problems = []
    video = pathlib.Path(str(record.get("video_path") or ""))
    if not record.get("video_path") or not video.is_file():
        problems.append("video file not found")
    else:
        if video.suffix.lower() not in VIDEO_SUFFIXES:
            problems.append(f"unsupported video type {video.suffix!r} (Reels want .mp4 or .mov)")
        if video.stat().st_size > MAX_VIDEO_BYTES:
            problems.append("video is over Meta's 1 GB limit")
    caption = str(record.get("caption") or "")
    if not caption.strip():
        problems.append("caption is empty")
    if "[ADD DESCRIPTION]" in caption:
        problems.append("caption still contains the [ADD DESCRIPTION] placeholder")
    if len(caption) > MAX_CAPTION_CHARS:
        problems.append(f"caption is {len(caption)} characters; the limit is {MAX_CAPTION_CHARS}")
    return problems


def publish_record(
    record: dict[str, Any],
    meta: MetaClient,
    stager: S3Stager,
    *,
    share_to_feed: bool,
    poll_interval: float,
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    video = pathlib.Path(record["video_path"])
    key, url = stager.stage(video)
    try:
        container_id = meta.create_reel_container(url, record["caption"], share_to_feed)
        record["container_id"] = container_id
        wait_for_container(meta, container_id, poll_interval, timeout, sleep=sleep, clock=clock)
        return meta.publish(container_id)
    finally:
        # Never leave a clip lying in the bucket, even after a failure; the
        # bucket's lifecycle rule is only the backstop.
        stager.discard(key)


# --- CLI ---------------------------------------------------------------------


def describe(record: dict[str, Any]) -> str:
    video = pathlib.Path(str(record.get("video_path") or "?"))
    caption = str(record.get("caption") or "").splitlines()
    first_line = caption[0] if caption else ""
    size = f"{video.stat().st_size / 1e6:.0f} MB" if video.is_file() else "missing"
    return f"{video.name} ({size}) - {first_line[:60]}"


def main(
    argv: Optional[list[str]] = None,
    *,
    meta: Optional[MetaClient] = None,
    stager: Optional[S3Stager] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--execute", action="store_true", help="Actually upload and publish. Default is a dry run.")
    parser.add_argument("--limit", type=int, default=None, help="Publish at most this many clips this run.")
    parser.add_argument("--share-to-feed", action=argparse.BooleanOptionalAction, default=True, help="Also show the Reel on the profile grid (default: yes).")
    parser.add_argument("--continue-on-error", action="store_true", help="Keep going after a clip fails (default: stop).")
    parser.add_argument("--api-version", default=DEFAULT_API_VERSION)
    parser.add_argument("--poll-interval", type=float, default=10.0, help="Seconds between processing-status checks.")
    parser.add_argument("--timeout", type=float, default=900.0, help="Seconds to wait for Meta to process one video.")
    parser.add_argument("--presign-seconds", type=int, default=3600, help="Lifetime of the staged download link.")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    load_dotenv(REPO_ROOT / ".env")

    if not args.manifest.is_file():
        print(f"Manifest not found: {args.manifest} (run tools/instagram_board_publisher.py first)")
        return 2
    try:
        records = read_manifest(args.manifest)
    except PublishError as error:
        print(error)
        return 2

    approved = [r for r in records if r.get("status") == "approved" and not r.get("published_media_id")]
    counts: dict[str, int] = {}
    for record in records:
        counts[str(record.get("status") or "?")] = counts.get(str(record.get("status") or "?"), 0) + 1
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    print(f"{args.manifest}: {len(records)} clips ({summary}); {len(approved)} approved and not yet published")
    if not approved:
        print("Nothing to publish. Set \"status\": \"approved\" on ready / check_climb clips you want posted.")
        return 0

    ready: list[dict[str, Any]] = []
    for record in approved:
        problems = validate(record)
        if problems:
            print(f"  [skip] {describe(record)}\n         " + "; ".join(problems))
        else:
            ready.append(record)
            print(f"  [ready] {describe(record)}")
    if args.limit is not None and len(ready) > args.limit:
        print(f"  ... limiting to the first {args.limit} of {len(ready)} ready clips")
        ready = ready[: args.limit]

    if not args.execute:
        print(f"\nDry run: nothing was uploaded or posted. Re-run with --execute to publish {len(ready)} clip(s).")
        return 0 if len(ready) == len(approved) else 1
    if not ready:
        print("\nNo clip is ready to publish.")
        return 1

    if meta is None or stager is None:
        token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        user_id = os.environ.get("INSTAGRAM_USER_ID", "")
        bucket = os.environ.get("INSTAGRAM_STAGING_BUCKET", "")
        missing = [name for name, value in (("INSTAGRAM_ACCESS_TOKEN", token), ("INSTAGRAM_USER_ID", user_id), ("INSTAGRAM_STAGING_BUCKET", bucket)) if not value]
        if missing:
            print("\nCannot publish: set " + ", ".join(missing) + " in the environment or .env (see the docstring).")
            return 2
        meta = meta or MetaClient(user_id, token, api_version=args.api_version)
        stager = stager or S3Stager(bucket, args.presign_seconds)

    failures = 0
    for record in ready:
        print(f"\nPublishing {describe(record)}")
        try:
            media_id = publish_record(
                record,
                meta,
                stager,
                share_to_feed=args.share_to_feed,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
                sleep=sleep,
                clock=clock,
            )
        except PublishError as error:
            failures += 1
            record["last_error"] = str(error)
            record["last_error_at"] = now_iso()
            write_manifest(args.manifest, records)
            print(f"  FAILED: {error}")
            if not args.continue_on_error:
                print("Stopping; fix the problem and re-run (already-published clips are skipped).")
                return 1
            continue
        record["status"] = "published"
        record["published_media_id"] = media_id
        record["published_at"] = now_iso()
        record.pop("last_error", None)
        record.pop("last_error_at", None)
        # Persist after every success so a crash later in the run cannot cause a
        # repost of this clip.
        write_manifest(args.manifest, records)
        print(f"  published as media {media_id}")

    print(f"\nDone: {len(ready) - failures} published, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
