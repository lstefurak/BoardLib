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


def test_title_overlay_is_compact_with_transparent_corners(tmp_path):
    target = tmp_path / "card.png"
    video.make_title(target, video.VideoInfo(640, 360, 10, 30, False), ["Example Climb", "V6 · 30° · 8/26"])
    with video.Image.open(target) as image:
        assert image.width == 576
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
    video.edit_video(source, output, 3.25, 5, ["Example", "V7 · 40° · 8/26"])
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
        return completed()

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
    monkeypatch.setattr(video, "_require_tools", lambda *names: None)
    monkeypatch.setattr(video, "edit_video", lambda *args: calls.append(args))
    assert video.main(["edit", str(source), "--output", str(tmp_path / "out.mp4"),
                       "--start", "0", "--name", "Example", "--grade", "V8",
                       "--angle", "30", "--sent", "8/26"]) == 0
    assert calls[0][3] == 5.0
    assert calls[0][4] == ["Example", "V8  ·  30°  ·  8/26"]
    assert calls[0][5] == "V8"
