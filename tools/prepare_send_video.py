#!/usr/bin/env python3
"""Prepare a shareable climbing clip with a bottom title overlay and a clean start.

The tool deliberately keeps the subjective decision (the first frame where the
climber is seated) reviewable.  ``sheet`` makes timestamped thumbnails; ``edit``
then trims at the chosen timestamp and overlays a two-line title for five seconds.

FFmpeg and ffprobe must be installed and available on PATH.  Pillow is the only
Python dependency and is already a BoardLib dependency.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont


# Cool-to-warm grade accents: higher grades use warmer, more saturated hues.
GRADE_COLORS = {
    "V6": "#67c5b5",   # teal
    "V7": "#70b7f0",   # blue
    "V8": "#b29bfa",   # purple
    "V9": "#f487c5",   # pink
    "V10": "#ff6b6b",  # vivid red
}


def grade_color(grade: str) -> str:
    """Keep +/- variants in their grade family; use neutral text outside V6-V10."""
    return GRADE_COLORS.get(grade.strip().upper().rstrip("+-"), "#d1d5db")


class VideoError(RuntimeError):
    """A user-actionable video preparation error."""


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration: float
    fps: float
    has_audio: bool


def _require_tools(*names: str) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise VideoError(f"missing required command(s): {', '.join(missing)}")


def check_environment() -> None:
    """Report the local dependencies needed before processing private media."""
    _require_tools("ffmpeg", "ffprobe")
    for name in ("ffmpeg", "ffprobe"):
        result = _run([name, "-version"], capture=True)
        print(result.stdout.splitlines()[0])
    print(f"Pillow {getattr(Image, '__version__', 'installed')}")
    print("Ready to prepare videos locally; no LLM or API key is required.")


def _run(command: list[str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=capture)


def _rate(value: str) -> float:
    numerator, _, denominator = value.partition("/")
    try:
        rate = float(numerator) / float(denominator or 1)
    except (ValueError, ZeroDivisionError):
        return 30.0
    return rate if rate > 0 else 30.0


def probe_video(path: Path) -> VideoInfo:
    result = _run(
        [
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ],
        capture=True,
    )
    data = json.loads(result.stdout)
    videos = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"]
    if not videos:
        raise VideoError(f"no video stream found in {path}")
    video = videos[0]
    duration_value = video.get("duration") or data.get("format", {}).get("duration")
    if duration_value is None:
        raise VideoError("ffprobe could not determine the video duration")
    width, height = int(video["width"]), int(video["height"])
    rotation = 0
    for side_data in video.get("side_data_list", []):
        rotation = int(side_data.get("rotation", rotation))
    if abs(rotation) % 180 == 90:
        width, height = height, width
    # H.264/yuv420p requires even dimensions.
    width, height = width - width % 2, height - height % 2
    return VideoInfo(
        width=width,
        height=height,
        duration=float(duration_value),
        fps=min(_rate(video.get("avg_frame_rate", "30/1")), 60.0),
        has_audio=any(stream.get("codec_type") == "audio" for stream in data.get("streams", [])),
    )


def _font(size: int):
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def make_title(path: Path, info: VideoInfo, lines: list[str], grade: str = "") -> None:
    """Render a compact, translucent two-line box for the bottom of the video."""
    width = max(2, int(info.width * 0.90))
    padding = max(8, int(min(info.width, info.height) * 0.025))
    size = max(12, int(min(info.width, info.height) * 0.052))
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    while True:
        fonts = [_font(size), _font(max(10, int(size * 0.78)))]
        boxes = [measure.textbbox((0, 0), line, font=font) for line, font in zip(lines, fonts)]
        if size <= 10 or all(box[2] - box[0] <= width - 2 * padding for box in boxes):
            break
        size -= 1
    gap = max(6, int(size * 0.35))
    height = sum(box[3] - box[1] for box in boxes) + gap + 2 * padding
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=padding, fill=(17, 24, 39, 225))
    y = padding
    for index, (line, font, box) in enumerate(zip(lines, fonts, boxes)):
        color = "#ffffff" if index == 0 else grade_color(grade)
        x = (width - (box[2] - box[0])) / 2 - box[0]
        draw.text((x, y - box[1]), line, fill=color, font=font)
        y += box[3] - box[1] + gap
    image.save(path)


def make_sheet(video: Path, output: Path, interval: float, columns: int) -> None:
    if interval <= 0 or columns <= 0:
        raise VideoError("--interval and --columns must be greater than zero")
    info = probe_video(video)
    with tempfile.TemporaryDirectory(prefix="boardlib-frames-") as temporary:
        pattern = str(Path(temporary) / "%05d.jpg")
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
            "-vf", f"fps=1/{interval},scale=320:-2", "-q:v", "3", pattern,
        ])
        frames = sorted(Path(temporary).glob("*.jpg"))
        if not frames:
            raise VideoError("FFmpeg did not extract any frames")
        with Image.open(frames[0]) as sample:
            tile_width, frame_height = sample.size
        tile_height = frame_height + 30
        rows = math.ceil(len(frames) / columns)
        sheet = Image.new("RGB", (tile_width * columns, tile_height * rows), "white")
        draw = ImageDraw.Draw(sheet)
        font = _font(16)
        for index, frame_path in enumerate(frames):
            with Image.open(frame_path) as frame:
                x, y = index % columns * tile_width, index // columns * tile_height
                sheet.paste(frame, (x, y))
                seconds = index * interval
                draw.text((x + 7, y + frame_height + 5), f"{seconds:.2f} s", fill="black", font=font)
        output.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(output)
    print(f"Wrote {output} ({len(frames)} frames across {info.duration:.2f} seconds)")


def edit_video(video: Path, output: Path, start: float, title_seconds: float, lines: list[str], grade: str = "") -> None:
    info = probe_video(video)
    if start < 0 or start >= info.duration:
        raise VideoError(f"--start must be between 0 and {info.duration:.2f} seconds")
    if title_seconds <= 0:
        raise VideoError("--title-seconds must be greater than zero")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="boardlib-title-") as temporary:
        card = Path(temporary) / "title.png"
        make_title(card, info, lines, grade)
        bottom_margin = max(4, int(info.height * 0.035))
        video_filter = (
            f"[0:v]fps={info.fps:.6f},scale={info.width}:{info.height}:force_original_aspect_ratio=decrease,"
            f"pad={info.width}:{info.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,setpts=PTS-STARTPTS[clip];"
            f"[clip][1:v]overlay=x=(W-w)/2:y=H-h-{bottom_margin}:"
            f"enable='lt(t,{title_seconds})':eof_action=repeat:format=auto,format=yuv420p[outv]"
        )
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-ss", str(start), "-i", str(video), "-i", str(card),
            "-filter_complex", video_filter, "-map", "[outv]",
        ]
        if info.has_audio:
            command += [
                "-map", "0:a:0", "-af",
                "aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS",
                "-c:a", "aac", "-b:a", "192k",
            ]
        else:
            command += ["-an"]
        command += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-map_metadata", "-1", str(output),
        ]
        _run(command)
    print(f"Wrote {output}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subcommands = root.add_subparsers(dest="command", required=True)
    subcommands.add_parser("check", help="verify FFmpeg, ffprobe, and Pillow are available")
    sheet = subcommands.add_parser("sheet", help="make timestamped thumbnails for choosing the seated start")
    sheet.add_argument("video", type=Path)
    sheet.add_argument("--output", "-o", type=Path, required=True)
    sheet.add_argument("--interval", type=float, default=0.5, help="seconds between frames (default: 0.5)")
    sheet.add_argument("--columns", type=int, default=4)

    edit = subcommands.add_parser("edit", help="trim a clip and overlay a two-line title at the bottom")
    edit.add_argument("video", type=Path)
    edit.add_argument("--output", "-o", type=Path, required=True)
    edit.add_argument("--start", type=float, required=True, help="first seated frame, in seconds")
    edit.add_argument("--name", required=True, help="climb name")
    edit.add_argument("--grade", required=True)
    edit.add_argument("--angle", required=True, help="for example 30 or 30°")
    edit.add_argument("--sent", required=True, help="display date, for example '8/26'")
    edit.add_argument("--title-seconds", type=float, default=5.0, help="overlay duration (default: 5 seconds)")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "check":
            check_environment()
            return 0
        _require_tools("ffmpeg", "ffprobe")
        if not args.video.is_file():
            raise VideoError(f"video not found: {args.video}")
        if args.command == "sheet":
            make_sheet(args.video, args.output, args.interval, args.columns)
        else:
            angle = args.angle if args.angle.endswith("°") else f"{args.angle}°"
            edit_video(args.video, args.output, args.start, args.title_seconds, [args.name, f"{args.grade}  ·  {angle}  ·  {args.sent}"], args.grade)
        return 0
    except (VideoError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
