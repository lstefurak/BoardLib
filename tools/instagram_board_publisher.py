#!/usr/bin/env python3
"""Build a reviewable Instagram publishing manifest from Google Photos Takeout.

This deliberately stops before publishing.  Instagram's API must fetch a video
from a public URL, so a human-reviewed manifest is the safe boundary between a
private photo archive and a later uploader.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Iterable, Optional


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".webm"}


@dataclass(frozen=True)
class Log:
    taken_at: datetime
    climb_name: str
    grade: str
    angle: str
    board: str
    ascent: bool


def parse_datetime(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_logs(path: Path) -> list[Log]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        required = {"date", "climb_name"}
        if not required.issubset(rows.fieldnames or []):
            raise ValueError("logbook CSV must contain date and climb_name columns")
        return [
            Log(
                taken_at=parse_datetime(row["date"]),
                climb_name=row["climb_name"].strip(),
                grade=(row.get("logged_grade") or row.get("displayed_grade") or "").strip(),
                angle=(row.get("angle") or "").strip(),
                board=(row.get("board") or "board").strip(),
                ascent=(row.get("is_ascent") or "").lower() in {"1", "true", "yes"},
            )
            for row in rows
        ]


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


def nearest_log(taken_at: datetime, logs: Iterable[Log], tolerance: timedelta) -> Optional[Log]:
    candidates = [(abs(log.taken_at - taken_at), log) for log in logs]
    if not candidates:
        return None
    distance, log = min(candidates, key=lambda item: item[0])
    return log if distance <= tolerance else None


def caption_for(taken_at: datetime, description: str, log: Optional[Log]) -> str:
    lines = [description] if description else []
    if log:
        detail = " · ".join(part for part in (log.climb_name, log.grade, f"{log.angle}°" if log.angle else "") if part)
        lines.extend((detail, f"{log.board.title()} board · {taken_at.strftime('%B %Y')}"))
        if not log.ascent:
            lines.append("Project session")
    else:
        lines.extend(("[ADD DESCRIPTION]", taken_at.strftime("%B %Y")))
    return "\n\n".join(lines)[:2200]


def build_manifest(takeout: Path, logs: list[Log], tolerance: timedelta) -> list[dict]:
    records = []
    videos = sorted(path for path in takeout.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES)
    for video in videos:
        taken_at, description, sidecar = takeout_metadata(video)
        log = nearest_log(taken_at, logs, tolerance)
        records.append(
            {
                "status": "needs_review",
                "video_path": str(video.resolve()),
                "sidecar_path": str(sidecar.resolve()) if sidecar else None,
                "taken_at": taken_at.isoformat(),
                "matched_log_at": log.taken_at.isoformat() if log else None,
                "climb_name": log.climb_name if log else None,
                "caption": caption_for(taken_at, description, log),
                "notes": [] if sidecar else ["No Takeout sidecar; timestamp came from file mtime."],
            }
        )
    return records


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("takeout", type=Path, help="unpacked Google Photos Takeout directory")
    parser.add_argument("--logbook", required=True, type=Path, help="BoardLib logbook CSV")
    parser.add_argument("--output", type=Path, default=Path("instagram-manifest.jsonl"))
    parser.add_argument("--tolerance-minutes", type=int, default=180)
    args = parser.parse_args(argv)
    if args.tolerance_minutes < 0:
        parser.error("--tolerance-minutes must be non-negative")

    records = build_manifest(args.takeout, read_logs(args.logbook), timedelta(minutes=args.tolerance_minutes))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"Wrote {len(records)} review records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
