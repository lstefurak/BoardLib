import csv
from datetime import datetime, timedelta, timezone
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

FIELDS = ["date", "climb_name", "logged_grade", "angle", "board", "is_ascent", "tries", "tries_total", "sessions_count", "is_repeat", "is_mirror"]
TAGS = publisher.DEFAULT_TAGS


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
            writer.writerow(
                {
                    "logged_grade": "7a/V6", "angle": "40", "board": "tension", "is_ascent": "true",
                    "tries": "1", "tries_total": "1", "sessions_count": "1", "is_repeat": "false", "is_mirror": "false",
                    **row,
                }
            )
    return csv_path


def log(**overrides):
    base = dict(
        taken_at=datetime(2026, 4, 29, 8, 54, tzinfo=timezone.utc), climb_name="Bring an Axe", grade="7a+/V7",
        angle="30", board="tension", ascent=True, tries=1, tries_total=1, sessions=1, repeat=False, mirror=False,
    )
    base.update(overrides)
    return publisher.Log(**base)


# --- captions ---------------------------------------------------------------


def test_caption_follows_the_tension_linking_convention():
    caption = publisher.caption_for(datetime.now(timezone.utc), "Bring an Axe V7 @ 30 (harder side)", log(tries=3, tries_total=3))
    assert caption == (
        '"Bring an Axe" V7 @ 30° on the Tension Board.\n'
        "Sent in 3 tries · April 29, 2026\n"
        "(harder side)\n"
        "\n"
        f"{TAGS}"
    )


def test_flash_gets_the_lightning_bolt_and_no_note_when_description_is_just_the_climb():
    caption = publisher.caption_for(datetime.now(timezone.utc), "Bring an Axe V7 @ 30", log())
    assert caption.splitlines()[1] == "⚡ Flash · April 29, 2026"
    assert caption.splitlines()[2] == ""  # nothing left over from the description


def test_send_line_variants():
    assert publisher.send_line(log()) == "⚡ Flash"
    assert publisher.send_line(log(tries=4, tries_total=4)) == "Sent in 4 tries"
    assert publisher.send_line(log(tries=2, tries_total=14, sessions=3)) == "Sent after 3 sessions (14 tries)"
    assert publisher.send_line(log(repeat=True)) == "Repeat send"
    assert publisher.send_line(log(repeat=True, tries=2)) == "Repeat send in 2 tries"
    assert publisher.send_line(log(ascent=False, tries=5, tries_total=5)) == "Project · 5 tries so far"
    assert publisher.send_line(log(ascent=False, tries=3, tries_total=9, sessions=2)) == "Project · 9 tries so far over 2 sessions"


def test_mirror_and_grade_and_day_of_month_formatting():
    caption = publisher.caption_for(datetime.now(timezone.utc), "", log(mirror=True, taken_at=datetime(2026, 6, 4, 9, 0, tzinfo=timezone.utc)))
    assert caption.startswith('"Bring an Axe" V7 @ 30° (mirror) on the Tension Board.\n⚡ Flash · June 4, 2026')
    assert publisher.v_grade("7a+/V7") == "V7"
    assert publisher.v_grade("V10") == "V10"
    assert publisher.v_grade("6c") == "6c"


def test_residual_note_survives_apostrophe_differences_and_drops_grade_angle():
    entry = log(climb_name="Twist ‘n Shout", grade="7b/V8")
    assert publisher.residual_note("Twist n' Shout V8 @ 30 (hard side)", entry) == "(hard side)"
    assert publisher.residual_note("Twist n' Shout - V8 @ 30", entry) == ""
    assert publisher.residual_note("🎈- V8 @ 30", log(climb_name="🎈", grade="7b/V8")) == ""
    # A grade that disagrees with the logbook is worth keeping for the reviewer.
    assert publisher.residual_note("Bring an Axe V8 @ 30", log()) == "V8"


def test_tags_and_board_label_are_configurable():
    caption = publisher.caption_for(datetime.now(timezone.utc), "", log(), tags="", board_label="Tension Board 2")
    assert caption == '"Bring an Axe" V7 @ 30° on the Tension Board 2.\n⚡ Flash · April 29, 2026'


# --- matching and statuses --------------------------------------------------


def test_description_match_is_ready_and_caption_uses_log_data(tmp_path):
    write_video(tmp_path, "clip.mp4", timestamp=1777467240, description="Bring an Axe V7 @ 30 (harder side)")
    csv_path = write_logs(tmp_path, [{"date": "2026-04-29T08:54:23.000", "climb_name": "Bring an Axe", "logged_grade": "7a+/V7", "angle": "30", "tries": "3", "tries_total": "3"}])

    record = publisher.build_manifest(tmp_path, publisher.read_logs(csv_path, ZoneInfo("America/New_York")), timedelta(minutes=60))[0]

    assert record["status"] == "ready"
    assert record["match_method"] == "description"
    assert record["send"] == "Sent in 3 tries"
    assert record["caption"].splitlines()[:3] == ['"Bring an Axe" V7 @ 30° on the Tension Board.', "Sent in 3 tries · April 29, 2026", "(harder side)"]
    assert record["caption_generated"] == record["caption"]


def test_time_only_match_is_check_climb_with_a_reason(tmp_path):
    write_video(tmp_path, "clip.mp4", timestamp=1777467240, description="Some Other Climb V5")
    csv_path = write_logs(tmp_path, [{"date": "2026-04-29T12:50:00+00:00", "climb_name": "Bring an Axe"}])

    record = publisher.build_manifest(tmp_path, publisher.read_logs(csv_path), timedelta(minutes=30))[0]

    assert record["status"] == "check_climb"
    assert record["climb_name"] == "Bring an Axe"
    assert any("does not mention 'Bring an Axe'" in note for note in record["notes"])


def test_unmatched_lists_same_day_climbs_and_keeps_the_description(tmp_path):
    write_video(tmp_path, "clip.mp4", timestamp=1777467240, description="great session")
    csv_path = write_logs(tmp_path, [{"date": "2026-04-29T00:00:00.000", "climb_name": "Midnight Logged"}])

    record = publisher.build_manifest(tmp_path, publisher.read_logs(csv_path, timezone.utc), timedelta(minutes=30))[0]

    assert record["status"] == "unmatched"
    assert record["same_day_climbs"] == ["Midnight Logged · V6 · 40°"]
    assert record["caption"].startswith("great session\nApril 29, 2026")


def test_unmatched_without_description_gets_the_placeholder(tmp_path):
    write_video(tmp_path, "unknown.mov")
    record = publisher.build_manifest(tmp_path, [], timedelta(hours=3))[0]
    assert record["status"] == "unmatched"
    assert publisher.PLACEHOLDER in record["caption"]
    assert record["sidecar_path"] is None


def test_naive_logbook_times_are_interpreted_in_the_logbook_timezone(tmp_path):
    write_video(tmp_path, "axe.mp4", timestamp=1777467240)  # 2026-04-29 12:54 UTC = 08:54 Eastern
    csv_path = write_logs(tmp_path, [{"date": "2026-04-29T08:52:00.000", "climb_name": "Bring an Axe"}])

    as_utc = publisher.build_manifest(tmp_path, publisher.read_logs(csv_path), timedelta(minutes=60))[0]
    as_eastern = publisher.build_manifest(tmp_path, publisher.read_logs(csv_path, ZoneInfo("America/New_York")), timedelta(minutes=60))[0]

    assert as_utc["status"] == "unmatched"
    assert as_eastern["status"] == "check_climb" and as_eastern["climb_name"] == "Bring an Axe"


# --- re-runs ----------------------------------------------------------------


def test_rerun_keeps_published_and_skipped_records_verbatim(tmp_path):
    video = write_video(tmp_path, "clip.mp4", timestamp=1777467240, description="Bring an Axe")
    csv_path = write_logs(tmp_path, [{"date": "2026-04-29T12:50:00+00:00", "climb_name": "Bring an Axe"}])
    logs = publisher.read_logs(csv_path)
    previous = {"status": "published", "video_path": str(video.resolve()), "caption": "as posted", "published_media_id": "1"}

    record = publisher.build_manifest(tmp_path, logs, timedelta(minutes=30), existing={previous["video_path"]: previous})[0]

    assert record == previous


def test_rerun_refreshes_an_untouched_approved_caption_but_keeps_the_approval(tmp_path):
    video = write_video(tmp_path, "clip.mp4", timestamp=1777467240, description="Bring an Axe")
    csv_path = write_logs(tmp_path, [{"date": "2026-04-29T12:50:00+00:00", "climb_name": "Bring an Axe"}])
    logs = publisher.read_logs(csv_path)
    old = {"status": "approved", "video_path": str(video.resolve()), "climb_name": "Bring an Axe", "caption": "[ADD DESCRIPTION]\n\nApril 2026"}

    record = publisher.build_manifest(tmp_path, logs, timedelta(minutes=30), existing={old["video_path"]: old})[0]

    assert record["status"] == "approved"
    assert record["caption"].startswith('"Bring an Axe" V6 @ 40° on the Tension Board.')


def test_rerun_keeps_a_reviewer_edited_caption_or_climb(tmp_path):
    video = write_video(tmp_path, "clip.mp4", timestamp=1777467240, description="Bring an Axe")
    csv_path = write_logs(tmp_path, [{"date": "2026-04-29T12:50:00+00:00", "climb_name": "Bring an Axe"}])
    logs = publisher.read_logs(csv_path)
    path = str(video.resolve())

    edited_caption = {"status": "approved", "video_path": path, "climb_name": "Bring an Axe", "caption": "my own words", "caption_generated": "generated"}
    assert publisher.build_manifest(tmp_path, logs, timedelta(minutes=30), existing={path: edited_caption})[0] == edited_caption

    changed_climb = {"status": "approved", "video_path": path, "climb_name": "Something Else", "caption": "x", "caption_generated": "x"}
    assert publisher.build_manifest(tmp_path, logs, timedelta(minutes=30), existing={path: changed_climb})[0] == changed_climb


def test_cli_rerun_reads_the_existing_manifest_and_summarises_statuses(tmp_path, capsys):
    video = write_video(tmp_path / "takeout", "clip.mp4", timestamp=1777467240, description="Bring an Axe") if (tmp_path / "takeout").mkdir() is None else None
    csv_path = write_logs(tmp_path, [{"date": "2026-04-29T12:50:00+00:00", "climb_name": "Bring an Axe"}])
    output = tmp_path / "manifest.jsonl"
    output.write_text(json.dumps({"status": "skip", "video_path": str(video.resolve()), "caption": "nope"}) + "\n", encoding="utf-8")

    assert publisher.main([str(tmp_path / "takeout"), "--logbook", str(csv_path), "--output", str(output)]) == 0

    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "skip"
    assert "1 skip" in capsys.readouterr().out


def test_cli_rejects_unknown_timezone(tmp_path, capsys):
    csv_path = write_logs(tmp_path, [])
    try:
        publisher.main([str(tmp_path), "--logbook", str(csv_path), "--logbook-tz", "Mars/Olympus"])
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("expected argparse to reject the timezone")
    assert "unknown timezone" in capsys.readouterr().err
