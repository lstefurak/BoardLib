import importlib.util
import json
from pathlib import Path
import sys

import pytest


TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("prepare_send_batch", TOOLS / "prepare_send_batch.py")
batch = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(batch)


def write_manifest(path, records):
    path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")


def record(source, **updates):
    return {"video_path": str(source), "status": "approved", "video_outcome": "send",
            "video_reviewed_at": "2026-01-02T15:00:00Z", "match_method": "description",
            "climb_name": "Example climb", "caption": '"Example climb" V7 @ 30°',
            "taken_at": "2026-01-01T12:00:00Z", "start_seconds": 2.0, **updates}


@pytest.mark.parametrize("updates", [
    {"video_outcome": "fall"}, {"video_outcome": "uncertain"}, {"status": "check_climb"},
    {"match_method": "time"}, {"label_confirmed": False}, {"wrong_video": True},
    {"needs_label_confirmation": True}, {"video_reviewed_at": None}, {"published_media_id": "posted"},
])
def test_only_reviewed_confirmed_sends_are_eligible(tmp_path, updates):
    assert not batch.eligible(record(tmp_path / "clip.mp4", **updates))
    assert batch.eligible(record(tmp_path / "clip.mp4", match_method="time", label_confirmed=True))


def fake_media(monkeypatch, source, calls):
    from prepare_send_video import VideoInfo

    def probe(path):
        return VideoInfo(1080, 1920, 10 if path == source else 8, 30, path == source)

    def edit(original, output, start, seconds, lines, grade, **kwargs):
        calls.append((original.name, start, seconds, lines, kwargs))
        output.write_bytes(b"finished video")

    def ffmpeg(arguments):
        if arguments[-1] != "-":
            Path(arguments[-1]).write_bytes(b"preview image")

    monkeypatch.setattr(batch, "probe_video", probe)
    monkeypatch.setattr(batch, "edit_video", edit)
    monkeypatch.setattr(batch, "run_ffmpeg", ffmpeg)


def test_preparation_is_silent_verified_restartable_and_preserves_original(monkeypatch, tmp_path):
    source = tmp_path / "originals" / "clip.mp4"
    source.parent.mkdir()
    source.write_bytes(b"original video")
    manifest = tmp_path / "plan.jsonl"
    write_manifest(manifest, [record(source)])
    calls = []
    fake_media(monkeypatch, source, calls)
    output = tmp_path / "finished"
    assert batch.prepare_batch(manifest, output) == 1
    prepared = batch.read_manifest(manifest)[0]
    assert source.read_bytes() == b"original video"
    assert prepared["prepared"] and prepared["audio_removed"]
    assert prepared["title_position"] == "top"
    assert all(prepared["checks"].values())
    assert Path(prepared["video_path"]).parent == output
    assert calls[0][1:3] == (2.0, 5.0)
    assert calls[0][4]["keep_audio"] is False
    assert batch.prepare_batch(manifest, output) == 0
    assert len(calls) == 1
    Path(prepared["video_path"]).write_bytes(b"changed output")
    with pytest.raises(batch.VideoError, match="changed"):
        batch.prepare_batch(manifest, output)


def test_batch_orders_oldest_first_and_does_not_prepare_falls(monkeypatch, tmp_path):
    sources = [tmp_path / name for name in ("late.mp4", "early.mp4", "fall.mp4")]
    for source in sources:
        source.write_bytes(b"original")
    manifest = tmp_path / "plan.jsonl"
    write_manifest(manifest, [record(sources[0], taken_at="2026-02-01T12:00:00Z"),
                              record(sources[1]), record(sources[2], video_outcome="fall")])
    calls = []

    def render(row, output, config):
        calls.append(batch.source_path(row).name)
        return {"prepared": True}

    monkeypatch.setattr(batch, "render_record", render)
    assert batch.prepare_batch(manifest, tmp_path / "finished", limit=1) == 1
    assert calls == ["early.mp4"]


def test_output_collision_and_original_directory_are_rejected(tmp_path):
    manifest = tmp_path / "plan.jsonl"
    first, second = tmp_path / "a" / "same.mp4", tmp_path / "b" / "same.mov"
    write_manifest(manifest, [record(first), record(second)])
    with pytest.raises(batch.VideoError, match="collide"):
        batch.prepare_batch(manifest, tmp_path / "finished")
    write_manifest(manifest, [record(first)])
    with pytest.raises(batch.VideoError, match="separate"):
        batch.prepare_batch(manifest, first.parent)


def test_untracked_output_is_not_overwritten(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original")
    output = tmp_path / "finished"
    output.mkdir()
    target = output / "clip.mp4"
    target.write_bytes(b"previous export")
    row = record(source)
    config = batch.configuration(row, 5, "top", 24, 28)
    with pytest.raises(batch.VideoError, match="already exists"):
        batch.render_record(row, output, config)
    assert target.read_bytes() == b"previous export"
    assert source.read_bytes() == b"original"


def test_save_merges_preparation_without_erasing_new_publication_state(tmp_path):
    manifest = tmp_path / "plan.jsonl"
    original = record(tmp_path / "clip.mp4")
    write_manifest(manifest, [original])
    snapshot = batch.read_manifest(manifest)[0]
    write_manifest(manifest, [{**original, "status": "published", "published_media_id": "123", "permalink": "https://www.instagram.com/reel/example/"}])
    batch.save_preparation(manifest, snapshot, {"prepared": True, "audio_removed": True})
    row = batch.read_manifest(manifest)[0]
    assert row["status"] == "published" and row["published_media_id"] == "123"
    assert row["prepared"] and row["audio_removed"]
    write_manifest(manifest, [{**original, "start_seconds": 3}])
    with pytest.raises(batch.VideoError, match="review changed"):
        batch.save_preparation(manifest, snapshot, {"prepared": True})


def test_relative_paths_use_manifest_directory_and_duplicate_sources_fail(tmp_path):
    manifest = tmp_path / "plan.jsonl"
    write_manifest(manifest, [record("originals/clip.mp4")])
    assert batch.source_path(batch.read_manifest(manifest)[0]) == tmp_path / "originals" / "clip.mp4"
    write_manifest(manifest, [record("originals/clip.mp4"), record("originals/../originals/clip.mp4")])
    with pytest.raises(batch.VideoError, match="duplicate"):
        batch.read_manifest(manifest)


def test_gallery_escapes_metadata_and_never_links_outside_output_or_script_urls(tmp_path):
    output = tmp_path / "finished"
    output.mkdir()
    source = tmp_path / "source.mp4"
    target = output / "clip name.mp4"
    target.write_bytes(b"finished")
    secret = tmp_path / "private.jpg"
    secret.write_bytes(b"private")
    manifest = tmp_path / "plan.jsonl"
    row = record(source, source_video_path=str(source), video_path=str(target), prepared=True,
                 climb_name="<script>alert(1)</script>", phone_grid_preview_path=str(secret),
                 permalink="javascript:alert(1)")
    write_manifest(manifest, [row])
    ledger = tmp_path / "published.jsonl"
    write_manifest(ledger, [{**row, "published_media_id": "123", "status": "published",
                            "video_path": str(secret), "permalink": "https://www.instagram.com/reel/example/"}])
    page = batch.build_review(manifest, output, ledger).read_text()
    assert "&lt;script&gt;" in page and "<script>" not in page
    assert "clip%20name.mp4" in page and "private.jpg" not in page
    assert "javascript:" not in page
    assert "1 posted" in page and "https://www.instagram.com/reel/example/" in page


def test_verification_failure_never_exposes_prepared_output(monkeypatch, tmp_path):
    from prepare_send_video import VideoInfo
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original")
    output = tmp_path / "finished"
    output.mkdir()
    calls = []
    fake_media(monkeypatch, source, calls)
    monkeypatch.setattr(batch, "probe_video", lambda path: VideoInfo(1080, 1920, 10 if path == source else 8, 30, True))
    row = record(source)
    with pytest.raises(batch.VideoError, match="verification failed"):
        batch.render_record(row, output, batch.configuration(row, 5, "top", 24, 28))
    assert not list(output.iterdir())
    assert source.read_bytes() == b"original"


@pytest.mark.parametrize("field,value", [("taken_at", "2026-01-01T12:00:00"),
                                        ("video_reviewed_at", "2026-01-02T15:00:00"),
                                        ("video_reviewed_at", "not a date")])
def test_bad_timestamps_stop_before_any_render(monkeypatch, tmp_path, field, value):
    manifest = tmp_path / "plan.jsonl"
    good = record(tmp_path / "good.mp4")
    bad = record(tmp_path / "bad.mp4", taken_at="2026-01-03T12:00:00Z", **{field: value}) if field != "taken_at" else record(tmp_path / "bad.mp4", taken_at=value)
    write_manifest(manifest, [good, bad])
    calls = []
    monkeypatch.setattr(batch, "render_record", lambda *args: calls.append(args))
    with pytest.raises(batch.VideoError, match="date|timezone"):
        batch.prepare_batch(manifest, tmp_path / "finished")
    assert not calls


def test_preparation_normalizes_review_and_log_timestamp_fallbacks(monkeypatch, tmp_path):
    source = tmp_path / "originals" / "clip.mp4"
    source.parent.mkdir()
    source.write_bytes(b"original")
    row = record(source, video_reviewed_at=None, reviewed_at="2026-01-02T15:00:00Z",
                 taken_at=None, matched_log_at="2026-01-01T23:00:00-05:00")
    manifest = tmp_path / "plan.jsonl"
    write_manifest(manifest, [row])
    fake_media(monkeypatch, source, [])
    batch.prepare_batch(manifest, tmp_path / "finished")
    prepared = batch.read_manifest(manifest)[0]
    assert prepared["video_reviewed_at"] == "2026-01-02T15:00:00+00:00"
    assert prepared["taken_at"] == "2026-01-01T23:00:00-05:00"
    assert prepared["display_date"] == "1/1"
    assert batch.prepare_batch(manifest, tmp_path / "finished") == 0


def test_explicit_zero_angle_is_preserved_without_a_caption_angle(tmp_path):
    row = record(tmp_path / "clip.mp4", grade="V7", angle=0, caption="Climbing")
    assert batch.label_fields(row)["angle"] == "0"


@pytest.mark.parametrize("title_seconds", [0.01, 0.1, 5.0])
def test_cover_is_within_the_visible_title_frames(monkeypatch, tmp_path, title_seconds):
    source = tmp_path / "originals" / "clip.mp4"
    source.parent.mkdir()
    source.write_bytes(b"original")
    output = tmp_path / "finished"
    output.mkdir()
    fake_media(monkeypatch, source, [])
    times = []

    def ffmpeg(arguments):
        if "-ss" in arguments:
            times.append(float(arguments[arguments.index("-ss") + 1]))
            Path(arguments[-1]).write_bytes(b"preview")

    monkeypatch.setattr(batch, "run_ffmpeg", ffmpeg)
    row = record(source)
    fields = batch.render_record(row, output, batch.configuration(row, title_seconds, "top", 24, 28))
    assert len(times) == 2
    assert all(time <= max(0.0, title_seconds - 1 / 30) for time in times)
    if title_seconds < 1 / 30:
        assert times == [0, 0] and fields["cover_frame_ms"] == 0


@pytest.mark.parametrize("identity_field", ["video_review_source_identity", "source_identity"])
def test_replaced_review_source_cannot_inherit_old_send_decision(monkeypatch, tmp_path, identity_field):
    source = tmp_path / "originals" / "clip.mp4"
    source.parent.mkdir()
    source.write_bytes(b"reviewed original")
    from review_send_videos import _source_identity
    identity = _source_identity(source)
    row = record(source, **{identity_field: identity})
    batch.verify_review_identity(row)
    source.write_bytes(b"replaced video with different contents")
    output = tmp_path / "finished"
    output.mkdir()
    calls = []
    fake_media(monkeypatch, source, calls)
    with pytest.raises(batch.VideoError, match="human review"):
        batch.render_record(row, output, batch.configuration(row, 5, "top", 24, 28))
    assert not calls and not list(output.iterdir())


def test_prepared_resume_rechecks_review_source_identity_even_if_hash_matches(monkeypatch, tmp_path):
    import os
    from review_send_videos import _source_identity
    source = tmp_path / "originals" / "clip.mp4"
    source.parent.mkdir()
    source.write_bytes(b"reviewed original")
    manifest = tmp_path / "plan.jsonl"
    write_manifest(manifest, [record(source, video_review_source_identity=_source_identity(source))])
    fake_media(monkeypatch, source, [])
    output = tmp_path / "finished"
    batch.prepare_batch(manifest, output)
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    with pytest.raises(batch.VideoError, match="human review"):
        batch.prepare_batch(manifest, output)


@pytest.mark.parametrize("updates", [{"video_outcome": "fall"}, {"video_outcome": "uncertain"},
                                    {"label_confirmed": False}, {"match_method": "time"},
                                    {"status": "skip"}, {"needs_label_confirmation": True}])
def test_gallery_excludes_prepared_clips_with_withdrawn_reviews(tmp_path, updates):
    output = tmp_path / "finished"
    output.mkdir()
    target = output / "clip.mp4"
    target.write_bytes(b"prepared")
    manifest = tmp_path / "plan.jsonl"
    write_manifest(manifest, [record(tmp_path / "source.mp4", source_video_path=str(tmp_path / "source.mp4"),
                                     video_path=str(target), prepared=True, **updates)])
    page = batch.build_review(manifest, output).read_text()
    assert "0 videos" in page and "<article>" not in page and "clip.mp4" not in page


@pytest.mark.parametrize("status", ["skip", "needs_review"])
def test_published_journal_cannot_resurrect_a_withdrawn_manifest_status(tmp_path, status):
    output = tmp_path / "finished"
    output.mkdir()
    target = output / "clip.mp4"
    target.write_bytes(b"prepared")
    row = record(tmp_path / "source.mp4", source_video_path=str(tmp_path / "source.mp4"),
                 video_path=str(target), prepared=True, status=status)
    manifest, ledger = tmp_path / "plan.jsonl", tmp_path / "journal.jsonl"
    write_manifest(manifest, [row])
    write_manifest(ledger, [{**row, "status": "published", "published_media_id": "123",
                            "permalink": "https://www.instagram.com/reel/example/"}])
    page = batch.build_review(manifest, output, ledger).read_text()
    assert "0 videos" in page and "<article>" not in page and "clip.mp4" not in page
