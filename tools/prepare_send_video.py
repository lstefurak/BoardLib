#!/usr/bin/env python3
"""Prepare a shareable climbing clip with preview-safe titles and a clean start.

The tool deliberately keeps the subjective decision (the first frame where the
climber is seated) reviewable.  ``sheet`` makes timestamped thumbnails; ``edit``
then trims at the chosen timestamp and overlays a preview-safe title for five seconds.

FFmpeg and ffprobe must be installed and available on PATH.  Pillow is the only
Python dependency and is already a BoardLib dependency.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont


# Cool-to-warm grade accents: higher grades use warmer, more saturated hues.
GRADE_COLORS = {
    "V6": "#74d6a1",   # green
    "V7": "#c7e879",   # lime
    "V8": "#ffd45e",   # yellow
    "V9": "#ffa064",   # orange
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


def _separate_output(video: Path, output: Path) -> None:
    if video.resolve() == output.resolve() or (video.exists() and output.exists() and video.samefile(output)):
        raise VideoError("output must be separate from the original video")


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


@dataclass(frozen=True)
class TitleLayout:
    width: int
    padding: int
    name_lines: tuple[str, ...]
    name_font_size: int
    details_font_size: int
    details: str


def title_layout(info: VideoInfo, lines: list[str]) -> TitleLayout:
    """Use large fixed name text, at most two rows, and larger climb details."""
    if not lines or not lines[0].strip():
        raise VideoError("a climb name is required")
    width = max(2, int(info.width * 0.98))
    padding = max(8, int(min(info.width, info.height) * 0.016))
    available = width - 2 * padding
    name_size = max(12, round(min(info.width, info.height) * 0.085))
    details_size = max(name_size + 1, round(name_size * 1.30))
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    def fits(text: str, size: int = name_size) -> bool:
        box = measure.textbbox((0, 0), text, font=_font(size))
        return box[2] - box[0] <= available

    def shorten(text: str, suffix: str = "") -> str:
        if fits(text + suffix):
            return text + suffix
        ending = "..." + suffix
        while text and not fits(text.rstrip() + ending):
            text = text[:-1]
        return text.rstrip() + ending

    name = " ".join(lines[0].split())
    mirrored = bool(re.search(r"\s*\((?:mirror|mir)\)\s*$", name, re.I))
    if mirrored:
        name = re.sub(r"\s*\((?:mirror|mir)\)\s*$", "", name, flags=re.I)
    single = name + (" (mirror)" if mirrored else "")
    if fits(single):
        name_lines = (single,)
    else:
        # Reserve the suffix on the final row; never leave it detached or clipped.
        words = name.split()
        first = ""
        while words and fits((first + " " + words[0]).strip()):
            first = (first + " " + words.pop(0)).strip()
        if not first:
            # A single very long word still gets two large rows.
            rest = name
            while rest and fits(first + rest[0]):
                first += rest[0]
                rest = rest[1:]
        else:
            rest = " ".join(words)
        if mirrored and not rest:
            # Move the final word down so the second row includes name + (mir).
            if " " in first:
                first, _, rest = first.rpartition(" ")
            else:
                midpoint = max(1, len(first) // 2)
                first, rest = first[:midpoint], first[midpoint:]
        name_lines = (first, shorten(rest, " (mir)" if mirrored else ""))
    details = lines[1] if len(lines) > 1 else ""
    while details_size > name_size + 1 and details and not fits(details, details_size):
        details_size -= 1
    if details and not fits(details, details_size):
        raise VideoError("grade, angle, and date do not fit in the larger details row")
    return TitleLayout(width, padding, name_lines, name_size, details_size, details)


def make_title(path: Path, info: VideoInfo, lines: list[str], grade: str = "") -> None:
    """Render a nearly full-width card with large name and larger details rows."""
    layout = title_layout(info, lines)
    rendered = [(line, _font(layout.name_font_size), "#ffffff") for line in layout.name_lines]
    if layout.details:
        rendered.append((layout.details, _font(layout.details_font_size), grade_color(grade)))
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    boxes = [measure.textbbox((0, 0), line, font=font) for line, font, _ in rendered]
    gap = max(6, round(layout.name_font_size * 0.18))
    height = sum(box[3] - box[1] for box in boxes) + gap * (len(boxes) - 1) + 2 * layout.padding
    image = Image.new("RGBA", (layout.width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, layout.width - 1, height - 1), radius=layout.padding, fill=(17, 24, 39, 225))
    y = layout.padding
    for (line, font, color), box in zip(rendered, boxes):
        x = (layout.width - (box[2] - box[0])) / 2 - box[0]
        draw.text((x, y - box[1]), line, fill=color, font=font)
        y += box[3] - box[1] + gap
    image.save(path)


def make_sheet(video: Path, output: Path, interval: float, columns: int) -> None:
    _separate_output(video, output)
    if not math.isfinite(interval) or interval <= 0 or columns <= 0:
        raise VideoError("--interval and --columns must be greater than zero")
    info = probe_video(video)
    with tempfile.TemporaryDirectory(prefix="boardlib-frames-") as temporary:
        pattern = str(Path(temporary) / "%05d.jpg")
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(video),
            "-vf", f"setpts=PTS-STARTPTS,select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval})',scale=320:-2,showinfo",
            "-fps_mode", "vfr", "-q:v", "3", pattern,
        ]
        try:
            extracted = _run(command, capture=True)
        except subprocess.CalledProcessError as error:
            if "Unrecognized option 'fps_mode'" not in (error.stderr or ""):
                raise
            # FFmpeg before 5.1 uses -vsync; FFmpeg 9 removed that old option.
            command[command.index("-fps_mode")] = "-vsync"
            extracted = _run(command, capture=True)
        frames = sorted(Path(temporary).glob("*.jpg"))
        if not frames:
            raise VideoError("FFmpeg did not extract any frames")
        # The fps filter rounds frames into output intervals, which can make a
        # nominal 0-second sample show a later pose. Label selected source PTS.
        times = [float(value) for value in re.findall(r"\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:([\d.eE+-]+)", extracted.stderr or "")]
        if len(times) != len(frames) or any(not math.isfinite(value) or value < 0 for value in times):
            raise VideoError("could not match extracted frames to source timestamps")
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
                seconds = times[index]
                draw.text((x + 7, y + frame_height + 5), f"{seconds:.2f} s", fill="black", font=font)
        output.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(output)
    print(f"Wrote {output} ({len(frames)} frames across {info.duration:.2f} seconds)")


def edit_video(
    video: Path, output: Path, start: float, title_seconds: float,
    lines: list[str], grade: str = "", *, keep_audio: bool = False,
    title_bottom_margin_percent: float = 28.0,
    title_position: str = "top", title_top_margin_percent: float = 24.0,
) -> None:
    _separate_output(video, output)
    info = probe_video(video)
    if not math.isfinite(start) or start < 0 or start >= info.duration:
        raise VideoError(f"--start must be between 0 and {info.duration:.2f} seconds")
    if not math.isfinite(title_seconds) or title_seconds <= 0:
        raise VideoError("--title-seconds must be greater than zero")
    if not math.isfinite(title_bottom_margin_percent) or not 0 <= title_bottom_margin_percent <= 50:
        raise VideoError("--title-bottom-margin-percent must be between 0 and 50")
    if title_position not in {"top", "bottom"}:
        raise VideoError("--title-position must be top or bottom")
    if not math.isfinite(title_top_margin_percent) or not 0 <= title_top_margin_percent <= 50:
        raise VideoError("--title-top-margin-percent must be between 0 and 50")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="boardlib-title-") as temporary:
        card = Path(temporary) / "title.png"
        make_title(card, info, lines, grade)
        # Keep all rows inside a centered square preview, with room for its UI.
        square_crop_bottom = (info.height + min(info.width, info.height)) / 2
        preview_margin = math.ceil(info.height - square_crop_bottom + info.height * 0.06)
        bottom_margin = max(4, preview_margin, int(info.height * title_bottom_margin_percent / 100))
        square_crop_top = (info.height - min(info.width, info.height)) / 2
        top_margin = max(4, math.ceil(square_crop_top + info.height * 0.02),
                         int(info.height * title_top_margin_percent / 100))
        title_y = str(top_margin) if title_position == "top" else f"H-h-{bottom_margin}"
        with Image.open(card) as title_image:
            if title_image.height + (top_margin if title_position == "top" else bottom_margin) > info.height:
                raise VideoError("title box is too tall for the selected position")
        video_filter = (
            f"[0:v]fps={info.fps:.6f},scale={info.width}:{info.height}:force_original_aspect_ratio=decrease,"
            f"pad={info.width}:{info.height}:(ow-iw)/2:(oh-ih)/2,setsar=1,setpts=PTS-STARTPTS[clip];"
            f"[clip][1:v]overlay=x=(W-w)/2:y={title_y}:"
            f"enable='lt(t,{title_seconds})':eof_action=repeat:format=auto,format=yuv420p[outv]"
        )
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-ss", str(start), "-i", str(video), "-i", str(card),
            "-filter_complex", video_filter, "-map", "[outv]",
        ]
        if info.has_audio and keep_audio:
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

    edit = subcommands.add_parser("edit", help="trim a clip and overlay large preview-safe titles")
    edit.add_argument("video", type=Path)
    edit.add_argument("--output", "-o", type=Path, required=True)
    edit.add_argument("--start", type=float, required=True, help="first seated frame, in seconds")
    edit.add_argument("--name", required=True, help="climb name")
    edit.add_argument("--grade", required=True)
    edit.add_argument("--angle", required=True, help="for example 30 or 30°")
    edit.add_argument("--sent", required=True, help="display date, for example '8/26'")
    edit.add_argument("--title-seconds", type=float, default=5.0, help="overlay duration (default: 5 seconds)")
    edit.add_argument("--keep-audio", action="store_true", help="retain source audio (output is silent by default)")
    edit.add_argument("--title-bottom-margin-percent", type=float, default=28.0,
                      help="title bottom margin in percent (default: 28; preview crop minimum is enforced)")
    edit.add_argument("--title-position", choices=["top", "bottom"], default="top",
                      help="title position inside the preview crop (default: top)")
    edit.add_argument("--title-top-margin-percent", type=float, default=24.0,
                      help="title top margin in percent (default: 24; preview crop minimum is enforced)")
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
            edit_video(args.video, args.output, args.start, args.title_seconds,
                       [args.name, f"{args.grade}  ·  {angle}  ·  {args.sent}"], args.grade,
                       keep_audio=args.keep_audio,
                       title_bottom_margin_percent=args.title_bottom_margin_percent,
                       title_position=args.title_position,
                       title_top_margin_percent=args.title_top_margin_percent)
        return 0
    except (VideoError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
