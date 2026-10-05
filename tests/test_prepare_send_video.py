import importlib.util
import json
from pathlib import Path
import sys

import pytest


MODULE_PATH = Path(__file__).parents[1] / "tools" / "prepare_send_video.py"
SPEC = importlib.util.spec_from_file_location("prepare_send_video", MODULE_PATH)
video = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = video
SPEC.loader.exec_module(video)


def completed(stdout=""):
    return video.subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def test_probe_reads_rotation_audio_duration_and_rate(monkeypatch, tmp_path):
    payload = {
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001", "side_data_list": [{"rotation": -90}]},
            {"codec_type": "audio"},
        ],
        "format": {"duration": "12.75"},
    }
    monkeypatch.setattr(video, "_run", lambda *args, **kwargs: completed(json.dumps(payload)))
    info = video.probe_video(tmp_path / "clip.mov")
    assert (info.width, info.height) == (1080, 1920)
    assert info.duration == 12.75
    assert info.fps == pytest.approx(29.97, rel=0.001)
    assert info.has_audio


def test_title_overlay_uses_full_width_with_transparent_corners(tmp_path):
    target = tmp_path / "card.png"
    video.make_title(target, video.VideoInfo(640, 360, 10, 30, False), ["Example Climb", "V6 · 30° · 8/26"])
    with video.Image.open(target) as image:
        assert image.width == 627
        assert image.height < 120
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((image.width // 2, 1))[3] == 225


@pytest.mark.parametrize("has_audio", [True, False])
def test_edit_overlays_title_without_delaying_clip(monkeypatch, tmp_path, has_audio):
    commands = []
    monkeypatch.setattr(video, "probe_video", lambda path: video.VideoInfo(1080, 1920, 20, 30, has_audio))
    monkeypatch.setattr(video, "_run", lambda command, capture=False: commands.append(command) or completed())
    source, output = tmp_path / "in.mov", tmp_path / "out.mp4"
    source.write_bytes(b"x")
    video.edit_video(source, output, 3.25, 5, ["Example", "V7 · 40° · 8/26"], keep_audio=True)
    command = commands[-1]
    assert command[command.index("-ss") + 1] == "3.25"
    filters = command[command.index("-filter_complex") + 1]
    assert "enable='lt(t,5)'" in filters
    assert "overlay=" in filters
    assert "concat" not in filters
    assert "-loop" not in command
    assert command[command.index("-i") + 1] == str(source)
    if has_audio:
        assert "0:a:0" in command
        assert "channel_layouts=stereo" in command[command.index("-af") + 1]
    else:
        assert "-an" in command
        assert "-af" not in command
    assert command[command.index("-map_metadata") + 1] == "-1"


def test_edit_rejects_start_after_end(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "probe_video", lambda path: video.VideoInfo(640, 360, 4, 30, False))
    with pytest.raises(video.VideoError, match="between 0 and 4.00"):
        video.edit_video(tmp_path / "in.mov", tmp_path / "out.mp4", 4, 2.5, ["x"])


def test_check_reports_dependencies_and_no_model_requirement(monkeypatch, capsys):
    monkeypatch.setattr(video.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(video, "_run", lambda command, capture=False: completed(f"{command[0]} version 7.0\n"))
    assert video.main(["check"]) == 0
    output = capsys.readouterr().out
    assert "ffmpeg version 7.0" in output
    assert "ffprobe version 7.0" in output
    assert "no LLM or API key" in output


def test_sheet_closes_frame_handles_before_temporary_cleanup(monkeypatch, tmp_path):
    opened_frames = []
    temporary_paths = []
    real_open = video.Image.open
    real_temporary_directory = video.tempfile.TemporaryDirectory

    class CheckedTemporaryDirectory(real_temporary_directory):
        def __exit__(self, *args):
            try:
                assert opened_frames
                assert all(frame.fp is None for frame in opened_frames)
            finally:
                super().__exit__(*args)

    def track_open(path, *args, **kwargs):
        frame = real_open(path, *args, **kwargs)
        opened_frames.append(frame)
        return frame

    def extract_frames(command, capture=False):
        directory = Path(command[-1]).parent
        temporary_paths.append(directory)
        for index in range(1, 3):
            with video.Image.new("RGB", (320, 180), "red") as frame:
                frame.save(directory / f"{index:05d}.jpg")
        return video.subprocess.CompletedProcess([], 0, stdout="", stderr="[showinfo] n: 0 pts: 0 pts_time:0\n[showinfo] n: 1 pts: 15 pts_time:0.5\n")

    monkeypatch.setattr(video, "probe_video", lambda path: video.VideoInfo(640, 360, 1, 30, False))
    monkeypatch.setattr(video, "_run", extract_frames)
    monkeypatch.setattr(video.Image, "open", track_open)
    monkeypatch.setattr(video.tempfile, "TemporaryDirectory", CheckedTemporaryDirectory)

    output = tmp_path / "review" / "sheet.jpg"
    video.make_sheet(tmp_path / "clip.mov", output, interval=0.5, columns=2)

    assert all(not directory.exists() for directory in temporary_paths)
    with real_open(output) as sheet:
        assert sheet.size == (640, 210)


def test_cli_defaults_to_five_second_two_line_overlay(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    calls = []
    options = []
    monkeypatch.setattr(video, "_require_tools", lambda *names: None)
    monkeypatch.setattr(video, "edit_video", lambda *args, **kwargs: (calls.append(args), options.append(kwargs)))
    assert video.main(["edit", str(source), "--output", str(tmp_path / "out.mp4"),
                       "--start", "0", "--name", "Example", "--grade", "V8",
                       "--angle", "30", "--sent", "8/26"]) == 0
    assert calls[0][3] == 5.0
    assert calls[0][4] == ["Example", "V8  ·  30°  ·  8/26"]
    assert calls[0][5] == "V8"
    assert options[0] == {"keep_audio": False, "title_bottom_margin_percent": 28.0,
                          "title_position": "top", "title_top_margin_percent": 24.0}


def test_default_output_removes_existing_audio_and_raises_title(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(video, "probe_video", lambda path: video.VideoInfo(1080, 1920, 20, 30, True))
    monkeypatch.setattr(video, "_run", lambda command, capture=False: commands.append(command) or completed())
    video.edit_video(tmp_path / "in.mp4", tmp_path / "out.mp4", 3, 5, ["Example", "V8"])
    command = commands[-1]
    assert "-an" in command
    assert "0:a:0" not in command
    assert "-af" not in command
    assert "y=460:" in command[command.index("-filter_complex") + 1]


def test_large_name_wraps_and_preserves_mirror_on_second_row():
    info = video.VideoInfo(1080, 1920, 20, 30, False)
    layout = video.title_layout(info, ["Knights of Cydonia (mirror)", "V8 · 30° · 4/29"])
    assert len(layout.name_lines) == 2
    assert layout.name_lines[-1].endswith(" (mir)")
    assert " ".join(layout.name_lines).replace(" (mir)", "") == "Knights of Cydonia"
    assert layout.details_font_size > layout.name_font_size
    assert layout.name_font_size >= 90


def test_long_name_truncates_second_row_and_keeps_mirror():
    info = video.VideoInfo(1080, 1920, 20, 30, False)
    name = "Knights of Cydonia with an extremely long additional climb name (mirror)"
    layout = video.title_layout(info, [name, "V8 · 30° · 4/29"])
    assert len(layout.name_lines) == 2
    assert layout.name_lines[-1].endswith("... (mir)")
    measure = video.ImageDraw.Draw(video.Image.new("RGB", (1, 1)))
    for line in layout.name_lines:
        box = measure.textbbox((0, 0), line, font=video._font(layout.name_font_size))
        assert box[2] - box[0] <= layout.width - 2 * layout.padding


def test_lower_requested_margin_still_keeps_all_rows_inside_preview(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(video, "probe_video", lambda path: video.VideoInfo(1080, 1920, 20, 30, False))
    monkeypatch.setattr(video, "_run", lambda command, capture=False: commands.append(command) or completed())
    video.edit_video(tmp_path / "in.mp4", tmp_path / "out.mp4", 0, 5,
                     ["Knights of Cydonia (mirror)", "V8 · 30° · 4/29"], title_bottom_margin_percent=0,
                     title_position="bottom")
    assert "y=H-h-536:" in commands[-1][commands[-1].index("-filter_complex") + 1]


def test_top_layout_stays_inside_preview_above_the_starting_climber(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(video, "probe_video", lambda path: video.VideoInfo(1080, 1920, 20, 30, False))
    monkeypatch.setattr(video, "_run", lambda command, capture=False: commands.append(command) or completed())
    video.edit_video(tmp_path / "in.mp4", tmp_path / "out.mp4", 0, 5,
                     ["Knights of Cydonia (mirror)", "V8 · 30° · 4/29"], title_top_margin_percent=0)
    assert "y=459:" in commands[-1][commands[-1].index("-filter_complex") + 1]


@pytest.mark.parametrize("operation", ["edit", "sheet"])
def test_output_cannot_overwrite_original_through_hardlink(tmp_path, operation):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original")
    alias = tmp_path / "alias.mp4"
    import os
    os.link(source, alias)
    with pytest.raises(video.VideoError, match="separate from the original"):
        if operation == "edit":
            video.edit_video(source, alias, 0, 5, ["Example"])
        else:
            video.make_sheet(source, alias, 0.5, 2)
    assert source.read_bytes() == b"original"


@pytest.mark.parametrize("start,title_seconds", [(float('nan'), 5), (float('inf'), 5), (0, float('nan')), (0, float('inf'))])
def test_edit_rejects_nonfinite_timestamps(monkeypatch, tmp_path, start, title_seconds):
    monkeypatch.setattr(video, "probe_video", lambda path: video.VideoInfo(1080, 1920, 20, 30, False))
    with pytest.raises(video.VideoError):
        video.edit_video(tmp_path / "source.mp4", tmp_path / "out.mp4", start, title_seconds, ["Example"])


@pytest.mark.skipif(video.shutil.which("ffmpeg") is None or video.shutil.which("ffprobe") is None, reason="FFmpeg is not installed")
def test_contact_sheet_first_sample_is_actual_first_frame(tmp_path):
    source = tmp_path / "synthetic.mp4"
    video._run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=64x96:r=4:d=0.25",
                "-f", "lavfi", "-i", "color=blue:s=64x96:r=4:d=3.75", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "ultrafast", str(source)])
    target = tmp_path / "sheet.jpg"
    video.make_sheet(source, target, interval=2.0, columns=2)
    with video.Image.open(target) as sheet:
        red, _, blue = sheet.getpixel((100, 100))
        assert red > 200 and blue < 30
        red, _, blue = sheet.getpixel((420, 100))
        assert blue > 200 and red < 30
