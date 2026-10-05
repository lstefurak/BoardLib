#!/usr/bin/env python3
"""Prepare reviewed sends locally and build a finished-video review gallery.

Use an explicit JSONL manifest and output directory. Originals are never replaced;
only approved, reviewed sends with confirmed climb labels are prepared. Run the
publisher after preparation, or use its separate publication ledger while rendering.
FFmpeg/ffprobe and Pillow are required for preparation, but not gallery generation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import quote, urlparse

from prepare_send_video import VideoError, edit_video, probe_video, title_layout


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def source_path(record: dict[str, Any]) -> Path:
    value = record.get("source_video_path") or record.get("video_path")
    if not isinstance(value, str) or not value.strip():
        raise VideoError("each record needs source_video_path or video_path")
    return Path(value).expanduser().resolve()


def source_key(record: dict[str, Any]) -> str:
    return os.path.normcase(str(source_path(record)))


def verify_review_identity(record: dict[str, Any]) -> None:
    """Use the same path/size/mtime fingerprint as the human-review tool."""
    identities = [record[field] for field in ("video_review_source_identity", "source_identity")
                  if record.get(field) is not None]
    if not identities:
        return
    source = source_path(record)
    if not source.is_file():
        raise VideoError("the reviewed source video is missing")
    stat = source.stat()
    value = f"{source_key(record)}\0{stat.st_size}\0{stat.st_mtime_ns}"
    actual = hashlib.sha256(value.encode("utf-8")).hexdigest()
    if any(not isinstance(identity, str) or identity != actual for identity in identities):
        raise VideoError("the source video changed since its human review; review it again")


def read_manifest(path: Path) -> list[dict[str, Any]]:
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise VideoError(f"manifest line {number} must be an object")
        for field in ("source_video_path", "video_path", "cover_path", "phone_grid_preview_path"):
            if record.get(field):
                asset = Path(str(record[field])).expanduser()
                record[field] = str((asset if asset.is_absolute() else path.parent / asset).resolve())
        records.append(record)
    keys = [source_key(record) for record in records]
    if len(keys) != len(set(keys)):
        raise VideoError("duplicate source records in the manifest")
    return records


def eligible(record: dict[str, Any]) -> bool:
    label_ok = record.get("label_confirmed") is True or (
        record.get("match_method") == "description" and record.get("label_confirmed") is not False
    )
    return bool(
        record.get("status") == "approved"
        and not record.get("published_media_id")
        and record.get("video_outcome") == "send"
        and (record.get("video_reviewed_at") or record.get("reviewed_at"))
        and label_ok and not record.get("wrong_video")
        and not record.get("needs_label_confirmation")
        and str(record.get("climb_name") or "").strip()
    )


def aware_timestamp(raw: Any, description: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise VideoError(f"{description} must be an ISO date with a timezone")
    try:
        timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise VideoError(f"invalid {description}") from error
    if timestamp.tzinfo is None:
        raise VideoError(f"{description} must include a timezone")
    return timestamp


def capture_timestamp(record: dict[str, Any]) -> datetime:
    return aware_timestamp(record.get("taken_at") or record.get("matched_log_at"), "capture or log date")


def chronological(record: dict[str, Any]) -> float:
    return capture_timestamp(record).timestamp()


def label_fields(record: dict[str, Any]) -> dict[str, str]:
    caption = str(record.get("caption") or "")
    grade_match = re.search(r"\b(V\d+[+-]?)\s*@", caption, re.I)
    angle_match = re.search(r"@\s*(\d+(?:\.\d+)?)\s*°", caption)
    grade = str(record.get("grade") or (grade_match.group(1) if grade_match else "")).strip()
    explicit_angle = record.get("angle")
    angle = str(explicit_angle if explicit_angle is not None and explicit_angle != "" else
                (angle_match.group(1) if angle_match else "")).strip().rstrip("°")
    date = str(record.get("display_date") or "").strip()
    if not date:
        timestamp = capture_timestamp(record)
        date = f"{timestamp.month}/{timestamp.day}"
    if not grade or not angle:
        raise VideoError("a reviewed send needs grade and angle, explicitly or in its caption")
    name = str(record["climb_name"]).strip()
    if re.search(r"\((?:mirror|mir)\)", caption, re.I) and not re.search(r"\((?:mirror|mir)\)\s*$", name, re.I):
        name += " (mirror)"
    return {"name": name, "grade": grade, "angle": angle, "display_date": date}


def configuration(record: dict[str, Any], title_seconds: float, position: str,
                  top_margin: float, bottom_margin: float) -> dict[str, Any]:
    try:
        start = float(record["start_seconds"])
    except (KeyError, ValueError, TypeError) as error:
        raise VideoError("a reviewed start_seconds is required") from error
    if not math.isfinite(start):
        raise VideoError("start_seconds must be finite")
    if not math.isfinite(title_seconds) or title_seconds <= 0:
        raise VideoError("title_seconds must be positive and finite")
    if position not in {"top", "bottom"}:
        raise VideoError("title position must be top or bottom")
    if any(not math.isfinite(margin) or not 0 <= margin <= 50 for margin in (top_margin, bottom_margin)):
        raise VideoError("title margins must be finite and between 0 and 50")
    return {"start_seconds": start, "title_seconds": title_seconds,
            "title_position": position, "title_top_margin_percent": top_margin,
            "title_bottom_margin_percent": bottom_margin, "audio_removed": True,
            "taken_at": capture_timestamp(record).isoformat(),
            "video_reviewed_at": aware_timestamp(record.get("video_reviewed_at") or record.get("reviewed_at"),
                                                 "video review date").isoformat(),
            **label_fields(record)}


def run_ffmpeg(arguments: list[str]) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", *arguments],
                   check=True, capture_output=True, text=True)


def asset_paths(source: Path, output_dir: Path) -> tuple[Path, Path, Path]:
    target = output_dir / (source.stem + ".mp4")
    return target, target.with_name(target.stem + "-cover.jpg"), target.with_name(target.stem + "-phone-grid.jpg")


def verify_prepared(record: dict[str, Any], output_dir: Path, config: dict[str, Any]) -> None:
    verify_review_identity(record)
    source = source_path(record)
    target, cover, phone = asset_paths(source, output_dir)
    if record.get("preparation_config") != config or Path(str(record.get("video_path"))).resolve() != target:
        raise VideoError("prepared output uses a different directory or edit configuration; use a new batch/output directory")
    expected = ((source, "source_sha256"), (target, "output_sha256"),
                (cover, "cover_sha256"), (phone, "phone_grid_preview_sha256"))
    for path, field in expected:
        if not path.is_file() or not record.get(field) or digest(path) != record[field]:
            raise VideoError("a previously prepared source or output changed; review it before preparing again")


def render_record(record: dict[str, Any], output_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    source = source_path(record)
    target, cover, phone = asset_paths(source, output_dir)
    if not source.is_file():
        raise VideoError("the original source video is missing")
    verify_review_identity(record)
    if source in (target, cover, phone) or any(path.exists() for path in (target, cover, phone)):
        raise VideoError("an output already exists; use a new output directory rather than replacing originals or untracked exports")
    original_hash = digest(source)
    if record.get("source_sha256") and record["source_sha256"] != original_hash:
        raise VideoError("the source changed since review")
    source_info = probe_video(source)
    start = config["start_seconds"]
    if not 0 <= start < source_info.duration:
        raise VideoError("the reviewed start is outside the original video")
    lines = [config["name"], f'{config["grade"]}  ·  {config["angle"]}°  ·  {config["display_date"]}']
    layout = title_layout(source_info, lines)
    with tempfile.TemporaryDirectory(prefix=".boardlib-preparing-", dir=output_dir) as temporary:
        temporary_dir = Path(temporary)
        temporary_video, temporary_cover, temporary_phone = asset_paths(source, temporary_dir)
        edit_video(source, temporary_video, start, config["title_seconds"], lines, config["grade"],
                   keep_audio=False, title_position=config["title_position"],
                   title_top_margin_percent=config["title_top_margin_percent"],
                   title_bottom_margin_percent=config["title_bottom_margin_percent"])
        info = probe_video(temporary_video)
        duration_ok = abs(info.duration - (source_info.duration - start)) <= max(0.15, 2.0 / info.fps)
        if info.has_audio or not duration_ok or (info.width, info.height) != (source_info.width, source_info.height):
            raise VideoError("output verification failed (audio, duration, or dimensions)")
        run_ffmpeg(["-xerror", "-i", str(temporary_video), "-map", "0:v:0", "-f", "null", "-"])
        frame_seconds = 1.0 / info.fps
        # Leave a full frame of room before either the label or video ends.
        # An overlay shorter than one frame uses frame zero, where t=0 is enabled.
        cover_time = min(1.0, max(0.0, info.duration - frame_seconds),
                         max(0.0, config["title_seconds"] - frame_seconds))
        run_ffmpeg(["-y", "-ss", str(cover_time), "-i", str(temporary_video), "-frames:v", "1",
                    "-vf", "scale=540:-2", str(temporary_cover)])
        # A centered 3:4 crop reproduces the phone's profile grid without stretching.
        phone_filter = ("crop=min(iw\\,ih*3/4):min(ih\\,iw*4/3),"
                        "scale=300:400:force_original_aspect_ratio=decrease,"
                        "pad=300:400:(ow-iw)/2:(oh-ih)/2,setsar=1")
        run_ffmpeg(["-y", "-ss", str(cover_time), "-i", str(temporary_video), "-frames:v", "1",
                    "-vf", phone_filter, str(temporary_phone)])
        if digest(source) != original_hash:
            raise VideoError("the original source changed while preparing its output")
        verify_review_identity(record)
        fields = {
            "prepared": True, "prepared_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "source_video_path": str(source), "video_path": str(target),
            "taken_at": config["taken_at"], "video_reviewed_at": config["video_reviewed_at"],
            "source_sha256": original_hash, "output_sha256": digest(temporary_video),
            "cover_sha256": digest(temporary_cover), "phone_grid_preview_sha256": digest(temporary_phone),
            "source_duration_seconds": source_info.duration, "output_duration_seconds": info.duration,
            "output_width": info.width, "output_height": info.height,
            "grade": config["grade"], "angle": config["angle"], "display_date": config["display_date"],
            "audio_removed": True, "title_seconds": config["title_seconds"],
            "title_position": config["title_position"], "title_top_margin_percent": config["title_top_margin_percent"],
            "title_bottom_margin_percent": config["title_bottom_margin_percent"],
            "preparation_config": config, "cover_frame_ms": round(cover_time * 1000),
            "cover_path": str(cover), "phone_grid_preview_path": str(phone),
            "title_layout": {"width": layout.width, "padding": layout.padding,
                             "name_lines": list(layout.name_lines), "name_font_size": layout.name_font_size,
                             "details_font_size": layout.details_font_size, "details": layout.details},
            "checks": {"source_unchanged": True, "dimensions_match": True, "duration_matches_trim": True,
                       "no_audio": True, "full_decode_passed": True, "original_ending_preserved": True,
                       "cover_generated": True, "phone_grid_preview_generated": True},
        }
        # Never expose half-rendered files to the publisher/gallery.
        for temporary_path, destination in ((temporary_cover, cover), (temporary_phone, phone), (temporary_video, target)):
            if destination.exists():
                raise VideoError("another writer created an output while preparing; stop concurrent preparations")
            os.replace(temporary_path, destination)
    return fields


def save_preparation(manifest: Path, original: dict[str, Any], fields: dict[str, Any]) -> None:
    """Re-read and merge preparation fields, preserving publication fields.

    Replacement is atomic. Uncoordinated writers must not share this manifest:
    use the publisher's separate ledger or sequence publishing after preparation.
    """
    before = manifest.read_bytes()
    records = read_manifest(manifest)
    matches = [row for row in records if source_key(row) == source_key(original)]
    if len(matches) != 1:
        raise VideoError("source record disappeared from the manifest")
    row = matches[0]
    review_fields = ("start_seconds", "climb_name", "grade", "angle", "display_date", "caption",
                     "video_outcome", "video_reviewed_at", "label_confirmed", "match_method",
                     "video_review_source_identity", "source_identity")
    if any(row.get(key) != original.get(key) for key in review_fields):
        raise VideoError("the clip review changed while preparing; use a fresh output directory after review")
    if row.get("status") != "approved" and row.get("status") != "published":
        raise VideoError("the clip is no longer approved")
    row.update(fields)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=manifest.parent,
                                         prefix=manifest.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
            handle.flush()
            os.fsync(handle.fileno())
        if manifest.read_bytes() != before:
            raise VideoError("the manifest changed during saving; stop concurrent writers before retrying")
        os.replace(temporary, manifest)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink()


def prepare_batch(manifest: Path, output_dir: Path, *, limit: int | None = None,
                  title_seconds: float = 5.0, position: str = "top",
                  top_margin: float = 24.0, bottom_margin: float = 28.0) -> int:
    manifest, output_dir = manifest.resolve(), output_dir.resolve()
    if limit is not None and limit < 1:
        raise VideoError("limit must be greater than zero")
    records = sorted((row for row in read_manifest(manifest) if eligible(row)), key=chronological)
    names = [source_path(row).stem.casefold() for row in records]
    if len(names) != len(set(names)):
        raise VideoError("source filenames collide; split those clips into separate batches/output directories")
    if any(output_dir == source_path(row).parent for row in records):
        raise VideoError("the output directory must be separate from the originals")
    configured = [(row, configuration(row, title_seconds, position, top_margin, bottom_margin)) for row in records]
    output_dir.mkdir(parents=True, exist_ok=True)
    completed = 0
    for record, config in configured:
        if record.get("prepared") is True:
            verify_prepared(record, output_dir, config)
            continue
        print(f'Preparing {record["climb_name"]}, start {config["start_seconds"]:.2f}s.', flush=True)
        fields = render_record(record, output_dir, config)
        save_preparation(manifest, record, fields)
        completed += 1
        if limit is not None and completed >= limit:
            break
    build_review(manifest, output_dir)
    print(f"Prepared {completed} verified sends locally; no videos published.", flush=True)
    return completed


def relative_asset(path: Any, output_dir: Path) -> str | None:
    if not path:
        return None
    resolved = Path(str(path)).resolve()
    try:
        relative = resolved.relative_to(output_dir)
    except ValueError:
        return None
    return quote(relative.as_posix(), safe="/") if resolved.is_file() else None


def build_review(manifest: Path, output_dir: Path, publication_ledger: Path | None = None) -> Path:
    output_dir = output_dir.resolve()
    rows = read_manifest(manifest)
    if publication_ledger is not None:
        updates = {source_key(row): row for row in read_manifest(publication_ledger)}
        # Publication records update presentation state, not trusted source/edit paths.
        keys = ("published_media_id", "published_at", "permalink")
        rows = [{**row, **{key: updates.get(source_key(row), {})[key] for key in keys
                          if key in updates.get(source_key(row), {})}} for row in rows]
    rows = [row for row in rows if row.get("prepared") is True
            and row.get("status") in {"approved", "published"}
            and eligible({**row, "status": "approved", "published_media_id": None})]
    rows.sort(key=chronological)
    esc = lambda value: html.escape(str(value), quote=True)
    cards = []
    posted = 0
    for number, row in enumerate(rows, 1):
        video = relative_asset(row.get("video_path"), output_dir)
        if not video:
            continue
        cover = relative_asset(row.get("cover_path"), output_dir)
        phone = relative_asset(row.get("phone_grid_preview_path"), output_dir)
        state = "Posted" if row.get("published_media_id") else "Ready"
        posted += state == "Posted"
        details = " · ".join(str(part) for part in (row.get("grade"), f'{row["angle"]}°' if row.get("angle") else "",
                                                    row.get("display_date")) if part)
        poster = f' poster="{esc(cover)}"' if cover else ""
        links = [f'<a href="{esc(video)}" download>Download video</a>']
        if phone:
            links.append(f'<a href="{esc(phone)}" target="_blank" rel="noopener">Phone preview</a>')
        permalink = str(row.get("permalink") or "")
        parsed = urlparse(permalink)
        if parsed.scheme == "https" and parsed.hostname in {"instagram.com", "www.instagram.com"}:
            links.append(f'<a href="{esc(permalink)}" target="_blank" rel="noopener">View on Instagram</a>')
        cards.append(f'<article><p class="state">{number:02d} · {state}</p>'
                     f'<video controls muted playsinline preload="none"{poster} src="{esc(video)}"></video>'
                     f'<div class="content"><h2>{esc(row.get("preparation_config", {}).get("name") or row.get("climb_name", "Climbing video"))}</h2>'
                     f'<p class="details">{esc(details)}</p><div class="links">{" ".join(links)}</div></div></article>')
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Finished climbing videos</title>
<style> *{{box-sizing:border-box}} body{{margin:0;background:#eef3f6;color:#162b36;font:16px/1.5 system-ui,sans-serif}}
main{{max-width:1200px;margin:auto;padding:30px 20px}} h1{{font-size:clamp(28px,4vw,44px);margin:0 0 10px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:22px;margin-top:24px}}
article{{border:1px solid #d9e4e8;border-radius:12px;overflow:hidden;background:white}} .state{{padding:0 16px;color:#326482;font-weight:700}}
video{{display:block;width:100%;aspect-ratio:9/16;object-fit:contain;background:#10181b}} .content{{padding:18px}}
h2{{font-size:22px;margin:0 0 10px}} .details{{font-size:20px;font-weight:700}} .links{{display:flex;gap:12px;flex-wrap:wrap}}
a{{color:#276674}} @media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><main>
<h1>Finished climbing videos</h1><p>Reviewed sends, oldest first. Check the cut and the label before publishing.</p>
<p>{len(cards)} videos · {posted} posted · {len(cards) - posted} ready</p>
<section class="grid" aria-label="Finished videos, oldest first">{"".join(cards)}</section></main></body></html>'''
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "review.html"
    target.write_text(page, encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="trim and label approved, reviewed sends; build the gallery")
    review = commands.add_parser("review", help="regenerate the finished-video gallery without rendering")
    for command in (prepare, review):
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--limit", type=int)
    prepare.add_argument("--title-seconds", type=float, default=5.0)
    prepare.add_argument("--title-position", choices=["top", "bottom"], default="top")
    prepare.add_argument("--title-top-margin-percent", type=float, default=24.0)
    prepare.add_argument("--title-bottom-margin-percent", type=float, default=28.0)
    review.add_argument("--publication-ledger", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare_batch(args.manifest, args.output_dir, limit=args.limit, title_seconds=args.title_seconds,
                          position=args.title_position, top_margin=args.title_top_margin_percent,
                          bottom_margin=args.title_bottom_margin_percent)
        else:
            print(build_review(args.manifest, args.output_dir, args.publication_ledger))
        return 0
    except VideoError as error:
        print(f"Batch stopped: {error}", file=sys.stderr)
    except (OSError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Batch stopped: {type(error).__name__}.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
