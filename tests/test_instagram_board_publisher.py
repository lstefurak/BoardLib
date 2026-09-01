import csv
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).parents[1] / "tools" / "instagram_board_publisher.py"
SPEC = importlib.util.spec_from_file_location("instagram_board_publisher", MODULE_PATH)
publisher = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = publisher
SPEC.loader.exec_module(publisher)


def test_build_manifest_matches_timestamp_and_preserves_description(tmp_path):
    video = tmp_path / "send.mp4"
    video.write_bytes(b"not-a-real-video")
    Path(str(video) + ".json").write_text(
        json.dumps({"description": "First go!", "photoTakenTime": {"timestamp": "1706875200"}})
    )
    csv_path = tmp_path / "logs.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "climb_name", "logged_grade", "angle", "board", "is_ascent"])
        writer.writeheader()
        writer.writerow({"date": "2024-02-02 12:05:00+00:00", "climb_name": "Pinch Test", "logged_grade": "V6", "angle": "40", "board": "tension", "is_ascent": "true"})

    records = publisher.build_manifest(tmp_path, publisher.read_logs(csv_path), timedelta(minutes=10))

    assert records[0]["climb_name"] == "Pinch Test"
    assert records[0]["caption"] == "First go!\n\nPinch Test · V6 · 40°\n\nTension board · February 2024"
    assert records[0]["status"] == "needs_review"


def test_unmatched_video_is_flagged_for_description(tmp_path):
    video = tmp_path / "unknown.mov"
    video.write_bytes(b"video")

    record = publisher.build_manifest(tmp_path, [], timedelta(hours=3))[0]

    assert "[ADD DESCRIPTION]" in record["caption"]
    assert record["sidecar_path"] is None
    assert record["notes"]
