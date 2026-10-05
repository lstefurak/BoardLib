#!/usr/bin/env python3
"""Publish verified sends oldest first, using a separate resumable journal.

The render manifest is read-only here, so it can be prepared concurrently.
Without --execute, inspection performs no network calls or journal writes.
Relative source/video paths are resolved against their manifest's directory.
Credentials use the same environment variables as instagram_publish.py.

Requests are journaled before being sent. An interrupted request without a
durable response stops the batch rather than risking a duplicate Reel. Keep
the journal between runs; do not remove unresolved entries to force a retry.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))
from instagram_publish import (
    DEFAULT_API_VERSION, MetaClient, S3Stager, load_dotenv, now_iso,
    read_manifest, validate, wait_for_container,
)


class BatchStop(RuntimeError):
    """A safe local explanation for stopping an ordered batch."""


def source_key(record: Dict[str, Any]) -> str:
    source = record.get("source_video_path") or record.get("video_path")
    if not isinstance(source, str) or not source:
        raise BatchStop("each record needs a source_video_path or original video_path")
    return os.path.normcase(str(Path(source).resolve()))


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise BatchStop("capture and publication times must be ISO dates with a timezone") from error
    if result.tzinfo is None:
        raise BatchStop("capture and publication times must include a timezone")
    return result.astimezone(timezone.utc)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def records_at(path: Path) -> List[Dict[str, Any]]:
    records = read_manifest(path)
    for record in records:
        if not isinstance(record, dict):
            raise BatchStop("manifest lines must be JSON objects")
        for field in ("source_video_path", "video_path"):
            value = record.get(field)
            if isinstance(value, str) and value:
                location = Path(value)
                record[field] = str((path.parent / location).resolve() if not location.is_absolute() else location.resolve())
    return records


def plan_records(path: Path) -> List[Dict[str, Any]]:
    records = records_at(path)
    keys = [source_key(record) for record in records]
    if len(set(keys)) != len(keys):
        raise BatchStop("the render plan contains duplicate sources")
    queued = [record for record in records if eligible(record)
              or record.get("published_media_id") or record.get("status") == "published"]
    return sorted(queued, key=lambda record: capture_time(record))


def capture_time(record: Dict[str, Any]) -> datetime:
    return timestamp(record.get("taken_at") or record.get("matched_log_at") or "")


def eligible(record: Dict[str, Any]) -> bool:
    """Ready for preparation/publication; prepared=False still keeps its place."""
    label_ok = record.get("label_confirmed") is True or (
        record.get("match_method") == "description" and record.get("label_confirmed") is not False
    )
    if (record.get("status") != "approved" or record.get("video_outcome") != "send"
            or record.get("wrong_video") or record.get("needs_label_confirmation")
            or not label_ok or not str(record.get("climb_name") or "").strip()):
        return False
    try:
        timestamp(record.get("video_reviewed_at") or record.get("reviewed_at") or "")
    except BatchStop:
        return False
    return True


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self.records = records_at(path) if path.exists() else []
        keys = [source_key(record) for record in self.records]
        ids = [str(record["published_media_id"]) for record in self.records if record.get("published_media_id")]
        if len(set(keys)) != len(keys) or len(set(ids)) != len(ids):
            raise BatchStop("publication journal contains duplicate sources or media IDs")

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return next((record for record in self.records if source_key(record) == key), None)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=str(self.path.parent),
                prefix=self.path.name + ".", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                for record in self.records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(self.path))
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def start(self, record: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        key = source_key(record)
        if self.get(key) is not None:
            raise BatchStop("a publication attempt for this source already exists")
        entry = dict(record, status="publishing", publishing_started_at=now_iso(),
                     publication_stage="before_staging", instagram_user_id=user_id)
        for field in ("published_media_id", "published_at", "container_id", "permalink"):
            entry.pop(field, None)
        self.records.append(entry)
        self.save()
        return entry

    def update(self, key: str, **fields: Any) -> Dict[str, Any]:
        entry = self.get(key)
        if entry is None:
            raise BatchStop("publication journal entry is missing")
        entry.update(fields)
        self.save()
        return entry


@contextmanager
def publisher_lock(ledger_path: Path):
    """The OS releases this lock even if the publisher process crashes."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.with_suffix(ledger_path.suffix + ".lock").open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise BatchStop("another publisher is already using this publication journal") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class JournaledMeta:
    """Persist irreversible request boundaries around an existing token client."""
    def __init__(self, client: MetaClient, ledger: Ledger):
        self.client = client
        self.user_id = client.user_id
        self.ledger = ledger
        self.active_key = None  # type: Optional[str]

    def bind(self, key: str) -> None:
        self.active_key = key

    def update(self, **fields: Any) -> None:
        if self.active_key is None:
            raise BatchStop("no source is bound to the publication journal")
        self.ledger.update(self.active_key, **fields)

    def _call(self, method: str, path: str, **fields: Any) -> Dict[str, Any]:
        return self.client._call(method, path, **fields)

    def container_status(self, container_id: str):
        return self.client.container_status(container_id)

    def create_reel_container(self, video_url: str, caption: str, **fields: Any) -> str:
        self.update(publication_stage="container_requested", container_requested_at=now_iso())
        container_id = self.client.create_reel_container(video_url, caption, True, **fields)
        self.update(container_id=container_id, publication_stage="container_created")
        return container_id

    def publish(self, container_id: str) -> str:
        self.update(publication_stage="publish_requested", publish_requested_at=now_iso())
        media_id = self.client.publish(container_id)
        # Persist before staging cleanup or post-publication verification can fail.
        self.update(status="published", published_media_id=media_id,
                    published_at=now_iso(), publication_stage="published")
        return media_id


def prepared_record(plan: Path, key: str, wait_seconds: float, poll_interval: float,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    deadline = clock() + wait_seconds
    while True:
        record = next((item for item in records_at(plan) if source_key(item) == key), None)
        if record is None:
            raise BatchStop("the next source is no longer in the render plan")
        if record.get("published_media_id") or record.get("status") == "published":
            return record
        if not eligible(record):
            raise BatchStop("the next source is not an approved send")
        if record.get("prepared") is True:
            return record
        if record.get("last_error") or record.get("render_error"):
            raise BatchStop("the oldest unprepared source has a preparation error")
        remaining = deadline - clock()
        if remaining <= 0:
            raise BatchStop("timed out waiting for the oldest source to finish preparation")
        sleep(min(poll_interval, remaining))


def verify_prepared(record: Dict[str, Any]) -> None:
    if record.get("status") != "approved" or record.get("video_outcome") != "send" or record.get("prepared") is not True:
        raise BatchStop("only prepared, approved sends can be published")
    if not record.get("video_reviewed_at"):
        raise BatchStop("the send needs a completed video review")
    timestamp(record["video_reviewed_at"])
    if (record.get("wrong_video") or record.get("needs_label_confirmation")
            or record.get("label_confirmed") is False
            or not (record.get("label_confirmed") is True or record.get("match_method") == "description")):
        raise BatchStop("the climb label has not been confirmed")
    required = {"source_unchanged", "dimensions_match", "duration_matches_trim", "no_audio", "full_decode_passed"}
    checks = record.get("checks")
    if not isinstance(checks, dict) or any(checks.get(name) is not True for name in required):
        raise BatchStop("the clip has incomplete or failed preparation checks")
    if record.get("title_position") != "top" or record.get("audio_removed") is not True:
        raise BatchStop("the clip must use the approved top title and silent audio")
    if validate(record):
        raise BatchStop("the prepared video or caption failed upload validation")
    for field, path_field in (("source_sha256", "source_video_path"), ("output_sha256", "video_path")):
        expected = record.get(field)
        path = Path(str(record.get(path_field) or ""))
        if not isinstance(expected, str) or len(expected) != 64 or not path.is_file() or digest(path) != expected:
            raise BatchStop("the source or prepared output changed after verification")


def cleanup_staging(stager: S3Stager, ledger: Ledger, key: str) -> None:
    entry = ledger.get(key)
    if entry and entry.get("staging_key") and not entry.get("staging_deleted"):
        if entry.get("staging_bucket") != stager.bucket:
            raise BatchStop("the pending staging cleanup belongs to a different bucket")
        stager.discard(str(entry["staging_key"]))
        ledger.update(key, staging_deleted=True)


def publish_new(record: Dict[str, Any], meta: JournaledMeta, stager: S3Stager,
                ledger: Ledger, plan: Path, args: argparse.Namespace,
                sleep: Callable[[float], None], clock: Callable[[], float]) -> str:
    key = source_key(record)
    meta.bind(key)
    object_key, url = stager.stage(Path(record["video_path"]))
    ledger.update(key, staging_key=object_key, staging_bucket=stager.bucket,
                  staging_deleted=False, publication_stage="staged")
    cover = {"cover_frame_ms": record["cover_frame_ms"]} if "cover_frame_ms" in record else {}
    container_id = meta.create_reel_container(url, record["caption"], **cover)
    wait_for_container(meta, container_id, args.poll_interval, args.timeout, sleep=sleep, clock=clock)
    ledger.update(key, container_finished=True)
    try:
        recovery_record(plan, key, ledger.get(key))
        return meta.publish(container_id)
    finally:
        cleanup_staging(stager, ledger, key)


def recover_published_id(meta: JournaledMeta, entry: Dict[str, Any], known_ids: Set[str]) -> str:
    """Reconcile a published container without sending a second publish request."""
    earliest = timestamp(entry["publishing_started_at"]) - timedelta(seconds=5)
    candidates = set()  # type: Set[str]
    after = None
    for _ in range(20):
        fields = {"fields": "id,caption,timestamp,media_product_type", "limit": 100}  # type: Dict[str, Any]
        if after:
            fields["after"] = after
        page = meta._call("GET", "{}/media".format(meta.user_id), **fields)
        items = page.get("data", [])
        for item in items:
            media_id = str(item.get("id") or "")
            if (media_id and media_id not in known_ids and item.get("caption") == entry.get("caption")
                    and item.get("media_product_type") == "REELS"
                    and timestamp(item.get("timestamp", "")) >= earliest):
                candidates.add(media_id)
        paging = page.get("paging", {})
        after = paging.get("cursors", {}).get("after") if paging.get("next") else None
        if not after:
            break
    else:
        raise BatchStop("publication reconciliation exceeded its page limit")
    if len(candidates) != 1:
        raise BatchStop("published container needs media ID reconciliation; no duplicate will be posted")
    return next(iter(candidates))


def recovery_record(plan: Path, key: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """A queued container cannot bypass a withdrawn or changed video review."""
    current = next((record for record in records_at(plan) if source_key(record) == key), None)
    if current is None:
        raise BatchStop("the journaled source is no longer in the render plan")
    verify_prepared(current)
    unchanged = ("caption", "source_sha256", "output_sha256", "start_seconds",
                 "climb_name", "grade", "angle", "title_position", "audio_removed", "preparation_config")
    if any(current.get(field) != entry.get(field) for field in unchanged):
        raise BatchStop("the source, output, caption, or edit changed after its container was created")
    return current


def recover_entry(meta: JournaledMeta, stager: S3Stager, ledger: Ledger, plan: Path, key: str,
                  known_ids: Set[str], args: argparse.Namespace,
                  sleep: Callable[[float], None], clock: Callable[[], float]) -> str:
    entry = ledger.get(key)
    container_id = str(entry.get("container_id") or "") if entry else ""
    if not container_id:
        raise BatchStop("an earlier container request has no durable ID; reconcile it before retrying")
    if entry.get("publication_stage") != "publish_requested":
        recovery_record(plan, key, entry)
    meta.bind(key)
    code, _ = meta.container_status(container_id)
    if code == "PUBLISHED":
        media_id = recover_published_id(meta, entry, known_ids)
        ledger.update(key, status="published", published_media_id=media_id,
                      published_at=now_iso(), publication_stage="recovered_published")
        cleanup_staging(stager, ledger, key)
        return media_id
    if code in {"ERROR", "EXPIRED"}:
        cleanup_staging(stager, ledger, key)
        raise BatchStop("the earlier container failed or expired; inspect the journal before retrying")
    if entry.get("publication_stage") == "publish_requested":
        if code == "FINISHED":
            cleanup_staging(stager, ledger, key)
        raise BatchStop("an earlier publish request is unresolved; no second request will be sent")
    if code != "FINISHED":
        wait_for_container(meta, container_id, args.poll_interval, args.timeout, sleep=sleep, clock=clock)
    ledger.update(key, container_finished=True)
    try:
        recovery_record(plan, key, entry)
        return meta.publish(container_id)
    finally:
        cleanup_staging(stager, ledger, key)


def verify_publication(meta: JournaledMeta, ledger: Ledger, key: str, media_id: str) -> str:
    item = meta._call("GET", media_id, fields="id,permalink,media_product_type,caption,timestamp")
    entry = ledger.get(key)
    if (entry is None or str(item.get("id")) != media_id or item.get("media_product_type") != "REELS"
            or item.get("caption") != entry.get("caption") or not item.get("permalink")):
        raise BatchStop("published media needs verification; its ID is preserved in the journal")
    permalink = str(item["permalink"])
    ledger.update(key, permalink=permalink, instagram_timestamp=item.get("timestamp"), publication_verified=True)
    return permalink


def main(argv: Optional[List[str]] = None, *, meta: Optional[MetaClient] = None,
         stager: Optional[S3Stager] = None, sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.monotonic) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="render plan (read only)")
    parser.add_argument("--ledger", type=Path, required=True, help="separate durable publication journal")
    parser.add_argument("--execute", action="store_true", help="publish using configured Instagram credentials")
    parser.add_argument("--expected-username", help="optional additional account guard")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--wait-seconds", type=float, default=600.0)
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--presign-seconds", type=int, default=3600)
    parser.add_argument("--api-version", default=DEFAULT_API_VERSION)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if (any(not math.isfinite(value) for value in (args.wait_seconds, args.poll_interval, args.timeout))
            or args.wait_seconds < 0 or not 0 < args.poll_interval <= 60 or args.timeout <= 0 or args.presign_seconds <= 0):
        parser.error("wait must be nonnegative; polling must be within 0–60 seconds; timeouts must be positive")
    plan, ledger_path = args.manifest.resolve(), args.ledger.resolve()
    if plan == ledger_path or (plan.exists() and ledger_path.exists() and os.path.samefile(str(plan), str(ledger_path))):
        parser.error("the publication journal must be separate from the render plan")
    ledger = None  # type: Optional[Ledger]
    active_key = None  # type: Optional[str]
    try:
        ordered = plan_records(plan)
        if not args.execute:
            ledger = Ledger(ledger_path)
            for record in ordered:
                entry = ledger.get(source_key(record))
                state = entry.get("status") if entry else record.get("status")
                print("{}: {}; prepared={}".format(record.get("climb_name", "Unnamed climb"), state, record.get("prepared") is True))
            print("Read-only inspection complete; no uploads or journal changes.")
            return 0
        load_dotenv(REPO_ROOT / ".env")
        if meta is None:
            token, user_id = os.environ.get("INSTAGRAM_ACCESS_TOKEN", ""), os.environ.get("INSTAGRAM_USER_ID", "")
            if not token or not user_id:
                raise BatchStop("Instagram credentials are missing")
            meta = MetaClient(user_id, token, api_version=args.api_version)
        if stager is None:
            bucket = os.environ.get("INSTAGRAM_STAGING_BUCKET", "")
            if not bucket:
                raise BatchStop("private staging configuration is missing")
            stager = S3Stager(bucket, args.presign_seconds)
        with publisher_lock(ledger_path):
            ledger = Ledger(ledger_path)
            client = JournaledMeta(meta, ledger)
            account = client._call("GET", "me", fields="id,username")
            if str(account.get("id")) != client.user_id or not account.get("username"):
                raise BatchStop("the token account does not match the configured Instagram user ID")
            if args.expected_username and str(account["username"]).casefold() != args.expected_username.lstrip("@").casefold():
                raise BatchStop("the token account does not match --expected-username")
            if any(entry.get("instagram_user_id") != client.user_id for entry in ledger.records):
                raise BatchStop("the publication journal belongs to a different Instagram account")
            planned_records = {source_key(record): record for record in records_at(plan)}
            planned_keys = set(planned_records)
            if any(not entry.get("published_media_id") and source_key(entry) not in planned_keys for entry in ledger.records):
                raise BatchStop("an unresolved journal source is missing from the render plan")
            queued_keys = {source_key(record) for record in ordered}
            # Keep every unresolved attempt visible even if its review was withdrawn.
            # Recovery revalidates it or reconciles an already-public container.
            ordered.extend(entry for entry in ledger.records
                           if not entry.get("published_media_id") and source_key(entry) not in queued_keys)
            ordered.sort(key=capture_time)
            known_ids = {str(entry["published_media_id"]) for entry in ledger.records + ordered if entry.get("published_media_id")}
            published = 0
            for original in ordered:
                key = source_key(original)
                active_key = key
                entry = ledger.get(key)
                if entry and entry.get("published_media_id"):
                    if original.get("published_media_id") and str(original["published_media_id"]) != str(entry["published_media_id"]):
                        raise BatchStop("the render plan and publication journal contain different public media IDs")
                    cleanup_staging(stager, ledger, key)
                    if not entry.get("publication_verified"):
                        verify_publication(client, ledger, key, str(entry["published_media_id"]))
                    continue
                if original.get("published_media_id") or original.get("status") == "published":
                    if entry:
                        raise BatchStop("the render plan is marked published but its journal attempt is unresolved")
                    continue
                if entry and entry.get("publication_stage") not in {"before_staging", "staged"}:
                    if entry.get("status") != "publishing":
                        raise BatchStop("an earlier unresolved publication entry needs review")
                    media_id = recover_entry(client, stager, ledger, plan, key, known_ids, args, sleep, clock)
                else:
                    record = prepared_record(plan, key, args.wait_seconds, args.poll_interval, sleep, clock)
                    if record.get("published_media_id") or record.get("status") == "published":
                        continue
                    verify_prepared(record)
                    if entry:
                        cleanup_staging(stager, ledger, key)
                        # No Meta request has been made; a fresh attempt is safe.
                        ledger.update(key, **dict(record, status="publishing", publication_stage="before_staging",
                                                  publishing_started_at=now_iso(), container_id=None))
                    else:
                        ledger.start(record, client.user_id)
                    print("Publishing {}, oldest first.".format(record.get("climb_name", "Unnamed climb")), flush=True)
                    media_id = publish_new(record, client, stager, ledger, plan, args, sleep, clock)
                if media_id in known_ids:
                    raise BatchStop("publication returned a previously recorded media ID")
                known_ids.add(media_id)
                permalink = verify_publication(client, ledger, key, media_id)
                published += 1
                print("Published: {}".format(permalink), flush=True)
                if args.limit is not None and published >= args.limit:
                    break
            print("Batch complete: {} sends published this run; the render plan was not changed.".format(published))
            return 0
    except BatchStop as error:
        print("Publication stopped: {}".format(error), file=sys.stderr)
        return 1
    except Exception as error:
        # Cloud SDK exceptions can embed tokens and presigned URLs; never print them.
        if ledger is not None and active_key is not None and ledger.get(active_key) is not None:
            try:
                ledger.update(active_key, last_error_type=type(error).__name__, last_error_at=now_iso())
            except Exception:
                pass
        print("Publication stopped: {}. Inspect the separate publication journal before retrying.".format(type(error).__name__), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
