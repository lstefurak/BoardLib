#!/usr/bin/env python3
"""Build a reviewable Instagram publishing manifest from Google Photos Takeout.

This deliberately stops before publishing.  Instagram's API must fetch a video
from a public URL, so a human-reviewed manifest is the safe boundary between a
private photo archive and a later uploader (``instagram_publish.py``).

Matching a clip to a logbook entry, in order of preference:

1. The clip's Google Photos description names the climb (e.g. "Bring an Axe V7
   @ 30").  A log entry for that climb on the same day (or the day either side)
   wins, nearest in time if there are several.
2. The nearest timed log entry within ``--tolerance-minutes``.
3. No match.  The record then lists every climb logged that day so the reviewer
   can name the right one in the description and re-run.

Captions follow the convention Tension's beta-video linking recognises:

    "Bring an Axe" V7 @ 30° on the Tension Board.
    Sent in 3 tries · April 29, 2026
    (harder side)

    @tensionclimbing #tensionboard #climbing #bouldering

The send line comes from the logbook: a lightning bolt for a flash, tries for a
single-session send, sessions (plus total tries) when it took longer, and
"Project" for an attempt that was not a send.  Anything in the Google Photos
description beyond the climb name/grade/angle is kept as a note.

Statuses set by this tool:
    ready        matched by the climb name in the description; approve it
    check_climb  matched by time only (or the description disagrees); confirm
                 the climb before approving
    unmatched    no log entry found; name the climb in the description
Statuses set by you / the uploader: ``approved``, ``skip``, ``published``.
Records in those three states are carried over untouched when you re-run.

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
DEFAULT_TAGS = "@tensionclimbing #tensionboard #climbing #bouldering"
DEFAULT_BOARD_LABEL = "Tension Board"
PLACEHOLDER = "[ADD DESCRIPTION]"
# Records in these states belong to the reviewer or the uploader; a re-run
# never regenerates them.
PRESERVED_STATUSES = {"approved", "skip", "published"}


@dataclass(frozen=True)
class Log:
    taken_at: datetime
    climb_name: str
    grade: str
    angle: str
    board: str
    ascent: bool
    tries: int = 1
    tries_total: int = 1
    sessions: int = 1
    repeat: bool = False
    mirror: bool = False
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


def _flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def _count(value: Optional[str], default: int = 1) -> int:
    try:
        return int(float(value)) if value not in (None, "") else default
    except ValueError:
        return default


def read_logs(path: Path, logbook_tz: tzinfo = timezone.utc) -> list[Log]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        required = {"date", "climb_name"}
        if not required.issubset(rows.fieldnames or []):
            raise ValueError("logbook CSV must contain date and climb_name columns")
        logs = []
        for row in rows:
            taken_at = parse_datetime(row["date"], logbook_tz)
            tries = _count(row.get("tries"))
            logs.append(
                Log(
                    taken_at=taken_at,
                    climb_name=row["climb_name"].strip(),
                    grade=(row.get("logged_grade") or row.get("displayed_grade") or "").strip(),
                    angle=(row.get("angle") or "").strip(),
                    board=(row.get("board") or "board").strip(),
                    ascent=_flag(row.get("is_ascent")),
                    tries=tries,
                    tries_total=max(tries, _count(row.get("tries_total"), tries)),
                    sessions=max(1, _count(row.get("sessions_count"))),
                    repeat=_flag(row.get("is_repeat")),
                    mirror=_flag(row.get("is_mirror")),
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


# --- Matching ---------------------------------------------------------------


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


# --- Captions ---------------------------------------------------------------


def v_grade(grade: str) -> str:
    """'7a+/V7' -> 'V7'; anything without a V part is returned as typed."""
    for part in re.split(r"[/\s]+", grade):
        if re.fullmatch(r"[Vv]\d+[+-]?", part):
            return part.upper()
    return grade


def send_line(log: Log) -> str:
    """The user-facing story of the send, from the logbook numbers."""
    tries = max(log.tries, 1)
    total = max(log.tries_total, tries)
    if not log.ascent:
        line = f"Project · {total} {'try' if total == 1 else 'tries'} so far"
        if log.sessions > 1:
            line += f" over {log.sessions} sessions"
        return line
    if log.repeat:
        return "Repeat send" + (f" in {tries} tries" if tries > 1 else "")
    if tries == 1 and total == 1 and log.sessions <= 1:
        return "⚡ Flash"
    if log.sessions > 1:
        return f"Sent after {log.sessions} sessions ({total} tries)"
    return f"Sent in {total} tries"


def residual_note(description: str, log: Log) -> str:
    """What the reviewer wrote beyond the climb name, grade and angle.

    Works token by token so "Twist n' Shout V8 @ 30 (hard side)" against the
    logbook's "Twist ‘n Shout" leaves just "(hard side)".
    """
    name_words = set(normalize_name(log.climb_name).split())
    grade_words = set(normalize_name(log.grade).split()) | {normalize_name(v_grade(log.grade))}
    angle_words = {normalize_name(log.angle), "deg", "degrees"}
    kept = []
    for token in description.split():
        words = normalize_name(token).split()
        if not words:
            continue  # punctuation-only tokens such as "@", "-", "·"
        if all(word in name_words for word in words):
            continue
        if all(word in grade_words or word in angle_words for word in words):
            continue
        kept.append(token)
    return " ".join(kept).strip(" -·,")


def caption_for(taken_at: datetime, description: str, log: Optional[Log], *, tags: str = DEFAULT_TAGS, board_label: str = DEFAULT_BOARD_LABEL) -> str:
    """Caption in the form Tension's beta-video linking recognises."""
    if log is None:
        lines = [description or PLACEHOLDER, taken_at.strftime("%B %d, %Y").replace(" 0", " ")]
        if tags:
            lines.extend(("", tags))
        return "\n".join(lines)[:2200]

    when = log.taken_at.strftime("%B %d, %Y").replace(" 0", " ")
    angle = f" @ {log.angle}°" if log.angle else ""
    grade = f" {v_grade(log.grade)}" if log.grade else ""
    mirror = " (mirror)" if log.mirror else ""
    header = f'"{log.climb_name}"{grade}{angle}{mirror} on the {board_label}.'
    lines = [header, f"{send_line(log)} · {when}"]
    note = residual_note(description, log)
    if note:
        lines.append(note)
    if tags:
        lines.extend(("", tags))
    return "\n".join(lines)[:2200]


def climb_label(log: Log) -> str:
    return " · ".join(part for part in (log.climb_name, v_grade(log.grade), f"{log.angle}°" if log.angle else "") if part)


# --- Manifest ---------------------------------------------------------------


def read_existing(path: Path) -> dict[str, dict]:
    """Previously written records keyed by video path (empty if none)."""
    if not path.is_file():
        return {}
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[str(record.get("video_path") or "")] = record
    return records


def build_manifest(
    takeout: Path,
    logs: list[Log],
    tolerance: timedelta,
    *,
    existing: Optional[dict[str, dict]] = None,
    tags: str = DEFAULT_TAGS,
    board_label: str = DEFAULT_BOARD_LABEL,
) -> list[dict]:
    existing = existing or {}
    records = []
    videos = sorted(path for path in takeout.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES)
    for video in videos:
        video_path = str(video.resolve())
        previous = existing.get(video_path)
        if previous and previous.get("status") in {"published", "skip"}:
            records.append(previous)
            continue

        taken_at, description, sidecar = takeout_metadata(video)
        log, method = find_log(taken_at, description, logs, tolerance)
        notes = [] if sidecar else ["No Takeout sidecar; timestamp came from file mtime."]
        candidates = []
        if log is None:
            status = "unmatched"
            candidates = sorted({climb_label(entry) for entry in same_day_logs(taken_at, logs)})
            if candidates:
                notes.append(
                    f"{len(candidates)} climb(s) logged that day but none within {int(tolerance.total_seconds() // 60)} min "
                    "or named in the description; name the climb in the description to match."
                )
            else:
                notes.append("Nothing logged that day; name the climb in the description or skip this clip.")
        elif method == "description":
            status = "ready"
            if log.has_time and abs(log.taken_at - taken_at) > tolerance:
                notes.append("Matched by climb name in the description; the log time is outside the tolerance window.")
            elif not log.has_time:
                notes.append("Matched by climb name in the description; that log entry has no time of day.")
        else:
            status = "check_climb"
            if description and normalize_name(log.climb_name) not in normalize_name(description):
                notes.append(f"Matched by time only and the description does not mention {log.climb_name!r}; confirm the climb.")
            else:
                notes.append(f"Matched by time only ({log.climb_name!r}); confirm the climb, then approve.")

        caption = caption_for(taken_at, description, log, tags=tags, board_label=board_label)
        record = {
            "status": status,
            "video_path": video_path,
            "sidecar_path": str(sidecar.resolve()) if sidecar else None,
            "taken_at": taken_at.isoformat(),
            "matched_log_at": log.taken_at.isoformat() if log else None,
            "match_method": method,
            "climb_name": log.climb_name if log else None,
            "send": send_line(log) if log else None,
            "same_day_climbs": candidates,
            "caption": caption,
            # Kept so a re-run can tell an untouched caption (safe to refresh)
            # from one the reviewer edited (kept verbatim).
            "caption_generated": caption,
            "notes": notes,
        }
        if previous and previous.get("status") == "approved":
            record = carry_over_approval(previous, record)
        records.append(record)

    # Reviewer/uploader records for files that are no longer in the folder are
    # the only history of what was posted or decided; keep them, flagged.
    seen = {record["video_path"] for record in records}
    for video_path, previous in existing.items():
        if video_path not in seen and previous.get("status") in PRESERVED_STATUSES:
            kept = dict(previous)
            notes = list(kept.get("notes") or [])
            if "Video file no longer present in the Takeout folder." not in notes:
                notes.append("Video file no longer present in the Takeout folder.")
            kept["notes"] = notes
            records.append(kept)
    return records


def carry_over_approval(previous: dict, fresh: dict) -> dict:
    """Keep an approval across re-runs without losing the reviewer's edits.

    The approval stands. The climb match and caption are refreshed only when
    the reviewer left them as generated; if they changed the climb or wrote
    their own caption, the whole previous record is kept.
    """
    reviewer_changed_climb = previous.get("climb_name") != fresh.get("climb_name")
    generated = previous.get("caption_generated")
    reviewer_edited_caption = generated is not None and previous.get("caption") != generated
    if reviewer_changed_climb or reviewer_edited_caption:
        return previous
    refreshed = dict(fresh)
    refreshed["status"] = "approved"
    return refreshed


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
    parser.add_argument("--tags", default=DEFAULT_TAGS, help="Mentions/hashtags appended to every caption ('' for none).")
    parser.add_argument("--board-label", default=DEFAULT_BOARD_LABEL, help="Board name used in the caption header.")
    args = parser.parse_args(argv)
    if args.tolerance_minutes < 0:
        parser.error("--tolerance-minutes must be non-negative")
    try:
        logbook_tz = resolve_tz(args.logbook_tz)
    except ValueError as error:
        parser.error(str(error))

    existing = read_existing(args.output)
    records = build_manifest(
        args.takeout,
        read_logs(args.logbook, logbook_tz),
        timedelta(minutes=args.tolerance_minutes),
        existing=existing,
        tags=args.tags,
        board_label=args.board_label,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    counts: dict[str, int] = {}
    for record in records:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    print(f"Wrote {len(records)} records to {args.output}: {summary}")
    if counts.get("check_climb"):
        print("  check_climb: matched by time only - confirm the climb (edit climb_name/caption if wrong), then set status to approved.")
    if counts.get("unmatched"):
        print("  unmatched: no log entry - name the climb in the clip's description and re-run, or set status to skip.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
