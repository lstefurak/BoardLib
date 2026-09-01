import csv
from datetime import timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


MODULE_PATH = Path(__file__).parents[1] / "tools" / "instagram_board_publisher.py"
SPEC = importlib.util.spec_from_file_location("instagram_board_publisher", MODULE_PATH)
publisher = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = publisher
SPEC.loader.exec_module(publisher)

FIELDS = ["date", "climb_name", "logged_grade", "angle", "board", "is_ascent"]


def write_video(tmp_path, name, *, timestamp=None, description=""):
    video = tmp_path / name
    video.write_bytes(b"not-a-real-video")
    if timestamp is not None:
        sidecar = {"description": description, "photoTakenTime": {"timestamp": str(timestamp)}}
        Path(str(video) + ".json").write_text(json.dumps(sidecar), encoding="utf-8")
    return video


def write_logs(tmp_path, rows):
    csv_path = tmp_path / "logs.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({"logged_grade": "V6", "angle": "40", "board": "tension", "is_ascent": "true", **row})
    return csv_path


def test_build_manifest_matches_timestamp_and_preserves_description(tmp_path):
    write_video(tmp_path, "send.mp4", timestamp=1706875200, description="First go!")
    csv_path = write_logs(tmp_path, [{"date": "2024-02-02 12:05:00+00:00", "climb_name": "Pinch Test"}])

    records = publisher.build_manifest(tmp_path, publisher.read_logs(csv_path), timedelta(minutes=10))

    assert records[0]["climb_name"] == "Pinch Test"
    assert records[0]["match_method"] == "time"
    assert records[0]["caption"] == "First go!\n\nPinch Test · V6 · 40°\n\nTension board · February 2024"
    assert records[0]["status"] == "needs_review"


def test_unmatched_video_is_flagged_for_description(tmp_path):
    write_video(tmp_path, "unknown.mov")

    record = publisher.build_manifest(tmp_path, [], timedelta(hours=3))[0]

    assert "[ADD DESCRIPTION]" in record["caption"]
    assert record["sidecar_path"] is None
    assert record["match_method"] is None
    assert record["notes"]


def test_naive_logbook_times_are_interpreted_in_the_logbook_timezone(tmp_path):
    # Clip taken 2026-04-29 12:54 UTC = 08:54 Eastern. The log says 08:52 with no
    # zone, which BoardLib exports as local time.
    write_video(tmp_path, "axe.mp4", timestamp=1777467240)
    csv_path = write_logs(tmp_path, [{"date": "2026-04-29T08:52:00.000", "climb_name": "Bring an Axe"}])

    as_utc = publisher.build_manifest(tmp_path, publisher.read_logs(csv_path), timedelta(minutes=60))[0]
    as_eastern = publisher.build_manifest(
        tmp_path, publisher.read_logs(csv_path, ZoneInfo("America/New_York")), timedelta(minutes=60)
    )[0]

    assert as_utc["climb_name"] is None  # 4 hours off when misread as UTC
    assert as_eastern["climb_name"] == "Bring an Axe"
    assert as_eastern["match_method"] == "time"


def test_description_naming_the_climb_beats_time_proximity(tmp_path):
    # Two climbs that day; the clip is closer in time to the wrong one, but the
    # Google Photos description names the right one.
    write_video(tmp_path, "clip.mp4", timestamp=1777467240, description="Twist n' Shout V8 @ 30 (hard side)")
    csv_path = write_logs(
        tmp_path,
        [
            {"date": "2026-04-29T12:50:00+00:00", "climb_name": "Bring an Axe"},
            {"date": "2026-04-29T09:10:00+00:00", "climb_name": "Twist N' Shout"},
        ],
    )

    record = publisher.build_manifest(tmp_path, publisher.read_logs(csv_path), timedelta(minutes=30))[0]

    assert record["climb_name"] == "Twist N' Shout"
    assert record["match_method"] == "description"
    assert record["caption"].startswith("Twist n' Shout V8 @ 30 (hard side)\n\nTwist N' Shout")
    assert "[ADD DESCRIPTION]" not in record["caption"]
    assert any("outside the tolerance" in note for note in record["notes"])


def test_unmatched_clip_lists_same_day_climbs_and_ignores_dateless_times(tmp_path):
    # Entries exported at exactly midnight have no time of day; they must not
    # be treated as "closest" but should be offered as candidates.
    write_video(tmp_path, "clip.mp4", timestamp=1777467240)  # 2026-04-29 12:54 UTC
    csv_path = write_logs(
        tmp_path,
        [
            {"date": "2026-04-29T00:00:00.000", "climb_name": "Midnight Logged"},
            {"date": "2026-04-29T00:00:00.000", "climb_name": "Also Midnight", "logged_grade": "V4"},
            {"date": "2026-04-28T12:50:00.000", "climb_name": "Yesterday"},
        ],
    )

    record = publisher.build_manifest(tmp_path, publisher.read_logs(csv_path, timezone.utc), timedelta(minutes=30))[0]

    assert record["climb_name"] is None
    assert record["same_day_climbs"] == ["Also Midnight · V4 · 40°", "Midnight Logged · V6 · 40°"]
    assert any("2 climb(s) logged that day" in note for note in record["notes"])


def test_time_match_that_contradicts_the_description_is_flagged(tmp_path):
    write_video(tmp_path, "clip.mp4", timestamp=1777467240, description="Some Climb Not In The Log V5")
    csv_path = write_logs(tmp_path, [{"date": "2026-04-29T12:50:00+00:00", "climb_name": "Bring an Axe"}])

    record = publisher.build_manifest(tmp_path, publisher.read_logs(csv_path), timedelta(minutes=30))[0]

    assert record["climb_name"] == "Bring an Axe"
    assert record["match_method"] == "time"
    assert any("does not mention 'Bring an Axe'" in note for note in record["notes"])


def test_cli_rejects_unknown_timezone(tmp_path, capsys):
    csv_path = write_logs(tmp_path, [])
    try:
        publisher.main([str(tmp_path), "--logbook", str(csv_path), "--logbook-tz", "Mars/Olympus"])
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("expected argparse to reject the timezone")
    assert "unknown timezone" in capsys.readouterr().err
