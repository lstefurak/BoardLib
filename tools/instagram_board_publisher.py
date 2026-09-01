#!/usr/bin/env python3
"""Build a reviewable Instagram publishing manifest from Google Photos Takeout.

This deliberately stops before publishing.  Instagram's API must fetch a video
from a public URL, so a human-reviewed manifest is the safe boundary between a
private photo archive and a later uploader.

Matching a clip to a logbook entry, in order of preference:

1. The clip's Google Photos description names the climb (e.g. "Bring an Axe V7
   @ 30").  A log entry for that climb on the same day (or the day either side)
   wins, nearest in time if there are several.
2. The nearest timed log entry within ``--tolerance-minutes``.
3. No match.  The record then lists every climb logged that day so the reviewer
   can name the right one in the description and re-run.

Logbook timestamps exported by BoardLib are timezone-naive local times, while
Takeout timestamps are UTC, so pass ``--logbook-tz`` (an IANA name such as
``America/New_York``, or ``local``) or nothing will line up.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
import json
from pathlib import Path
import re
from typing import Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".webm"}


@dataclass(frozen=True)
class Log:
    taken_at: datetime
    climb_name: str
    grade: str
    angle: str
    board: str
    ascent: bool
    # Some entries are exported with a date but no time (midnight). They can
    # still be matched by name or listed as same-day candidates, but never by
    # time proximity.
    has_time: bool = True


def resolve_tz(name: str) -> tzinfo:
    if name.lower() in {"utc", "z"}:
        return timezone.utc
    if name.lower() == "local":
        return datetime.now().astimezone().tzinfo or timezone.utc
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"unknown timezone {name!r}; use an IANA name like America/New_York, or 'local'") from error


def parse_datetime(value: str, default_tz: tzinfo = timezone.utc) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=default_tz)


def read_logs(path: Path, logbook_tz: tzinfo = timezone.utc) -> list[Log]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        required = {"date", "climb_name"}
        if not required.issubset(rows.fieldnames or []):
            raise ValueError("logbook CSV must contain date and climb_name columns")
        logs = []
        for row in rows:
            taken_at = parse_datetime(row["date"], logbook_tz)
            logs.append(
                Log(
                    taken_at=taken_at,
                    climb_name=row["climb_name"].strip(),
                    grade=(row.get("logged_grade") or row.get("displayed_grade") or "").strip(),
                    angle=(row.get("angle") or "").strip(),
                    board=(row.get("board") or "board").strip(),
                    ascent=(row.get("is_ascent") or "").lower() in {"1", "true", "yes"},
                    has_time=(taken_at.hour, taken_at.minute, taken_at.second) != (0, 0, 0),
                )
            )
        return logs


def sidecar_for(video: Path) -> Optional[Path]:
    candidates = (
        Path(str(video) + ".json"),
        video.with_suffix(video.suffix + ".supplemental-metadata.json"),
        video.with_suffix(".json"),
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def takeout_metadata(video: Path) -> tuple[datetime, str, Optional[Path]]:
    sidecar = sidecar_for(video)
    if sidecar:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        stamp = (data.get("photoTakenTime") or data.get("creationTime") or {}).get("timestamp")
        if stamp:
            taken_at = datetime.fromtimestamp(int(stamp), tz=timezone.utc)
        else:
            taken_at = datetime.fromtimestamp(video.stat().st_mtime, tz=timezone.utc)
        return taken_at, str(data.get("description") or "").strip(), sidecar
    return datetime.fromtimestamp(video.stat().st_mtime, tz=timezone.utc), "", None


def normalize_name(text: str) -> str:
    """Lower-case, punctuation-free form used to find climb names inside descriptions."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.casefold()).split())


def same_day_logs(taken_at: datetime, logs: Iterable[Log], days_either_side: int = 0) -> list[Log]:
    """Logs whose local date is the clip's local date (in the logbook's own zone)."""
    matches = []
    for log in logs:
        clip_date = taken_at.astimezone(log.taken_at.tzinfo).date()
        if abs((log.taken_at.date() - clip_date).days) <= days_either_side:
            matches.append(log)
    return matches


def nearest(taken_at: datetime, candidates: Iterable[Log]) -> Optional[Log]:
    scored = [(abs(log.taken_at - taken_at), log) for log in candidates]
    return min(scored, key=lambda item: item[0])[1] if scored else None


def find_log(taken_at: datetime, description: str, logs: list[Log], tolerance: timedelta) -> tuple[Optional[Log], Optional[str]]:
    """Return (log, method) where method is "description", "time" or None."""
    described = normalize_name(description)
    if described:
        nearby = same_day_logs(taken_at, logs, days_either_side=1)
        named = [log for log in nearby if normalize_name(log.climb_name) and normalize_name(log.climb_name) in described]
        # Prefer entries with a real time so several sends of one climb resolve
        # to the closest one; fall back to a date-only entry for that climb.
        timed = [log for log in named if log.has_time]
        chosen = nearest(taken_at, timed) or nearest(taken_at, named)
        if chosen:
            return chosen, "description"

    timed = [log for log in logs if log.has_time]
    chosen = nearest(taken_at, timed)
    if chosen and abs(chosen.taken_at - taken_at) <= tolerance:
        return chosen, "time"
    return None, None


def caption_for(taken_at: datetime, description: str, log: Optional[Log]) -> str:
    lines = [description] if description else ["[ADD DESCRIPTION]"]
    if log:
        detail = " · ".join(part for part in (log.climb_name, log.grade, f"{log.angle}°" if log.angle else "") if part)
        lines.extend((detail, f"{log.board.title()} board · {taken_at.strftime('%B %Y')}"))
        if not log.ascent:
            lines.append("Project session")
    else:
        lines.append(taken_at.strftime("%B %Y"))
    return "\n\n".join(lines)[:2200]


def climb_label(log: Log) -> str:
    return " · ".join(part for part in (log.climb_name, log.grade, f"{log.angle}°" if log.angle else "") if part)


def build_manifest(takeout: Path, logs: list[Log], tolerance: timedelta) -> list[dict]:
    records = []
    videos = sorted(path for path in takeout.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES)
    for video in videos:
        taken_at, description, sidecar = takeout_metadata(video)
        log, method = find_log(taken_at, description, logs, tolerance)
        notes = [] if sidecar else ["No Takeout sidecar; timestamp came from file mtime."]
        candidates = []
        if log is None:
            candidates = sorted({climb_label(entry) for entry in same_day_logs(taken_at, logs)})
            if candidates:
                notes.append(
                    f"{len(candidates)} climb(s) logged that day but none within {int(tolerance.total_seconds() // 60)} min "
                    "or named in the description; name the climb in the description to match."
                )
        elif method == "description" and log.has_time and abs(log.taken_at - taken_at) > tolerance:
            notes.append("Matched by climb name in the description; the log time is outside the tolerance window.")
        elif method == "description" and not log.has_time:
            notes.append("Matched by climb name in the description; that log entry has no time of day.")
        elif method == "time" and description and normalize_name(log.climb_name) not in normalize_name(description):
            # The reviewer wrote something, and it is not the climb we picked by
            # time. Either the description names a climb missing from the logbook
            # or the time match is wrong; a human has to decide.
            notes.append(f"Matched by time only; the description does not mention {log.climb_name!r}. Verify.")
        records.append(
            {
                "status": "needs_review",
                "video_path": str(video.resolve()),
                "sidecar_path": str(sidecar.resolve()) if sidecar else None,
                "taken_at": taken_at.isoformat(),
                "matched_log_at": log.taken_at.isoformat() if log else None,
                "match_method": method,
                "climb_name": log.climb_name if log else None,
                "same_day_climbs": candidates,
                "caption": caption_for(taken_at, description, log),
                "notes": notes,
            }
        )
    return records


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("takeout", type=Path, help="unpacked Google Photos Takeout directory")
    parser.add_argument("--logbook", required=True, type=Path, help="BoardLib logbook CSV")
    parser.add_argument("--output", type=Path, default=Path("instagram-manifest.jsonl"))
    parser.add_argument("--tolerance-minutes", type=int, default=180)
    parser.add_argument(
        "--logbook-tz",
        default="UTC",
        help="Timezone of naive logbook timestamps: an IANA name (America/New_York), 'local', or UTC (default).",
    )
    args = parser.parse_args(argv)
    if args.tolerance_minutes < 0:
        parser.error("--tolerance-minutes must be non-negative")
    try:
        logbook_tz = resolve_tz(args.logbook_tz)
    except ValueError as error:
        parser.error(str(error))

    records = build_manifest(args.takeout, read_logs(args.logbook, logbook_tz), timedelta(minutes=args.tolerance_minutes))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    matched = sum(1 for record in records if record["climb_name"])
    by_name = sum(1 for record in records if record["match_method"] == "description")
    print(f"Wrote {len(records)} review records to {args.output} ({matched} matched, {by_name} of them by climb name)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
