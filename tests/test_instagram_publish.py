import importlib.util
import json
from pathlib import Path
import sys

import pytest


MODULE_PATH = Path(__file__).parents[1] / "tools" / "instagram_publish.py"
SPEC = importlib.util.spec_from_file_location("instagram_publish", MODULE_PATH)
publish = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = publish
SPEC.loader.exec_module(publish)


class FakeMeta:
    """Scripted stand-in for MetaClient; records every call in order."""

    def __init__(self, statuses=("IN_PROGRESS", "FINISHED"), media_id="17900000000000001", fail_create=None):
        self.statuses = list(statuses)
        self.media_id = media_id
        self.fail_create = fail_create
        self.calls = []

    def create_reel_container(self, video_url, caption, share_to_feed):
        self.calls.append(("create", video_url, caption, share_to_feed))
        if self.fail_create:
            raise publish.PublishError(self.fail_create)
        return "container-1"

    def container_status(self, container_id):
        self.calls.append(("status", container_id))
        code = self.statuses.pop(0) if self.statuses else "IN_PROGRESS"
        return code, f"detail for {code}"

    def publish(self, container_id):
        self.calls.append(("publish", container_id))
        return self.media_id


class FakeStager:
    def __init__(self):
        self.staged = []
        self.discarded = []

    def stage(self, video):
        key = f"instagram/test/{video.name}"
        self.staged.append(key)
        return key, f"https://bucket.example/{key}?signed"

    def discard(self, key):
        self.discarded.append(key)


def write_manifest(tmp_path, records):
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return manifest


def read_manifest(manifest):
    return [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def clips(tmp_path):
    good = tmp_path / "send.mp4"
    good.write_bytes(b"video bytes")
    other = tmp_path / "other.mp4"
    other.write_bytes(b"more video bytes")
    return good, other


@pytest.fixture
def manifest(tmp_path, clips):
    good, other = clips
    return write_manifest(
        tmp_path,
        [
            {"status": "approved", "video_path": str(good), "caption": "Bring an Axe V7 @ 30\n\nTension board", "climb_name": "Bring an Axe"},
            {"status": "needs_review", "video_path": str(other), "caption": "[ADD DESCRIPTION]\n\nMay 2026"},
            {"status": "published", "video_path": str(other), "caption": "Already up", "published_media_id": "17800000000000009"},
        ],
    )


def run(manifest, *extra, meta=None, stager=None, clock=None):
    ticks = iter(range(0, 10_000, 1))
    return publish.main(
        ["--manifest", str(manifest), *extra],
        meta=meta,
        stager=stager,
        sleep=lambda seconds: None,
        clock=clock or (lambda: next(ticks)),
    )


def test_dry_run_reports_ready_clips_and_touches_nothing(manifest, capsys):
    before = manifest.read_text(encoding="utf-8")
    meta, stager = FakeMeta(), FakeStager()

    assert run(manifest, meta=meta, stager=stager) == 0

    out = capsys.readouterr().out
    assert "1 approved, 1 needs_review, 1 published" in out
    assert "1 approved and not yet published" in out
    assert "[ready] send.mp4" in out
    assert "Dry run: nothing was uploaded or posted" in out
    assert manifest.read_text(encoding="utf-8") == before
    assert meta.calls == [] and stager.staged == []


def test_execute_publishes_and_records_the_media_id(manifest, capsys):
    meta, stager = FakeMeta(statuses=["IN_PROGRESS", "IN_PROGRESS", "FINISHED"]), FakeStager()

    assert run(manifest, "--execute", meta=meta, stager=stager) == 0

    assert [call[0] for call in meta.calls] == ["create", "status", "status", "status", "publish"]
    create = meta.calls[0]
    assert create[1].startswith("https://bucket.example/instagram/test/send.mp4")
    assert create[2].startswith("Bring an Axe V7 @ 30")
    assert create[3] is True
    assert stager.discarded == stager.staged == ["instagram/test/send.mp4"]

    records = read_manifest(manifest)
    assert records[0]["status"] == "published"
    assert records[0]["published_media_id"] == "17900000000000001"
    assert records[0]["container_id"] == "container-1"
    assert records[0]["published_at"].endswith("+00:00")
    assert records[1]["status"] == "needs_review"  # untouched
    assert "published as media 17900000000000001" in capsys.readouterr().out


def test_rerun_skips_already_published_clips(manifest):
    meta, stager = FakeMeta(), FakeStager()
    assert run(manifest, "--execute", meta=meta, stager=stager) == 0

    meta2, stager2 = FakeMeta(), FakeStager()
    assert run(manifest, "--execute", meta=meta2, stager=stager2) == 0
    assert meta2.calls == [] and stager2.staged == []
    assert sum(1 for r in read_manifest(manifest) if r.get("published_media_id")) == 2


def test_no_share_to_feed_flag_is_passed_through(manifest):
    meta = FakeMeta()
    assert run(manifest, "--execute", "--no-share-to-feed", meta=meta, stager=FakeStager()) == 0
    assert meta.calls[0][3] is False


def test_processing_error_keeps_record_approved_and_cleans_up(manifest, capsys):
    meta, stager = FakeMeta(statuses=["IN_PROGRESS", "ERROR"]), FakeStager()

    assert run(manifest, "--execute", meta=meta, stager=stager) == 1

    assert "publish" not in [call[0] for call in meta.calls]
    assert stager.discarded == stager.staged
    record = read_manifest(manifest)[0]
    assert record["status"] == "approved"
    assert "published_media_id" not in record
    assert "could not process the video (ERROR)" in record["last_error"]
    assert "FAILED" in capsys.readouterr().out


def test_meta_error_on_create_is_reported_and_staged_file_removed(manifest):
    meta, stager = FakeMeta(fail_create="Meta API error 190: Invalid OAuth access token"), FakeStager()
    assert run(manifest, "--execute", meta=meta, stager=stager) == 1
    assert stager.discarded == stager.staged
    assert "Invalid OAuth access token" in read_manifest(manifest)[0]["last_error"]


def test_processing_timeout_is_a_clean_failure(manifest):
    meta = FakeMeta(statuses=[])  # never finishes
    clock_values = iter([0, 0, 1000, 1000, 1000])
    assert run(manifest, "--execute", "--timeout", "600", meta=meta, stager=FakeStager(), clock=lambda: next(clock_values)) == 1
    assert "timed out after 600s" in read_manifest(manifest)[0]["last_error"]


def test_successful_publish_clears_an_old_error(manifest):
    records = read_manifest(manifest)
    records[0]["last_error"] = "old failure"
    write_manifest(manifest.parent, records)
    assert run(manifest, "--execute", meta=FakeMeta(), stager=FakeStager()) == 0
    assert "last_error" not in read_manifest(manifest)[0]


def test_limit_publishes_only_the_first_n(tmp_path, clips):
    good, other = clips
    manifest = write_manifest(
        tmp_path,
        [
            {"status": "approved", "video_path": str(good), "caption": "one"},
            {"status": "approved", "video_path": str(other), "caption": "two"},
        ],
    )
    meta = FakeMeta()
    assert run(manifest, "--execute", "--limit", "1", meta=meta, stager=FakeStager()) == 0
    assert [call[0] for call in meta.calls].count("create") == 1
    statuses = [r["status"] for r in read_manifest(manifest)]
    assert statuses == ["published", "approved"]


def test_validation_problems_are_skipped_and_fail_the_dry_run(tmp_path, clips, capsys):
    good, _ = clips
    manifest = write_manifest(
        tmp_path,
        [
            {"status": "approved", "video_path": str(tmp_path / "missing.mp4"), "caption": "x"},
            {"status": "approved", "video_path": str(good), "caption": "[ADD DESCRIPTION]\n\nMay 2026"},
            {"status": "approved", "video_path": str(good), "caption": "y" * 2201},
            {"status": "approved", "video_path": str(good), "caption": "fine"},
        ],
    )
    assert run(manifest, meta=FakeMeta(), stager=FakeStager()) == 1
    out = capsys.readouterr().out
    assert "video file not found" in out
    assert "[ADD DESCRIPTION] placeholder" in out
    assert "caption is 2201 characters" in out
    assert out.count("[ready]") == 1


def test_execute_without_credentials_does_nothing(manifest, monkeypatch, capsys):
    for name in ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID", "INSTAGRAM_STAGING_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(publish, "REPO_ROOT", manifest.parent)  # no .env there
    assert publish.main(["--manifest", str(manifest), "--execute"]) == 2
    assert "INSTAGRAM_ACCESS_TOKEN" in capsys.readouterr().out
    assert read_manifest(manifest)[0]["status"] == "approved"


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def test_meta_client_sends_token_in_body_and_surfaces_api_errors():
    session = FakeSession(
        [
            FakeResponse(200, {"id": "container-9"}),
            FakeResponse(200, {"status_code": "FINISHED", "status": "ok"}),
            FakeResponse(400, {"error": {"message": "Invalid OAuth access token.", "code": 190}}),
        ]
    )
    client = publish.MetaClient("178", "secret-token", api_version="v23.0", session=session)

    assert client.create_reel_container("https://x/y.mp4", "cap", True) == "container-9"
    assert client.container_status("container-9") == ("FINISHED", "ok")
    with pytest.raises(publish.PublishError, match="Meta API error 190: Invalid OAuth access token"):
        client.publish("container-9")

    method, url, kwargs = session.requests[0]
    assert (method, url) == ("POST", "https://graph.instagram.com/v23.0/178/media")
    assert kwargs["data"]["media_type"] == "REELS"
    assert kwargs["data"]["share_to_feed"] == "true"
    assert kwargs["data"]["access_token"] == "secret-token"
    assert "secret-token" not in url
    method, url, kwargs = session.requests[1]
    assert (method, url) == ("GET", "https://graph.instagram.com/v23.0/container-9")
    assert kwargs["params"]["fields"] == "status_code,status"
    assert session.requests[2][1].endswith("/178/media_publish")


def test_s3_stager_uploads_presigns_and_deletes(tmp_path):
    class FakeS3:
        def __init__(self):
            self.calls = []

        def upload_file(self, filename, bucket, key, ExtraArgs=None):
            self.calls.append(("upload", filename, bucket, key, ExtraArgs))

        def generate_presigned_url(self, op, Params, ExpiresIn):
            self.calls.append(("presign", op, Params, ExpiresIn))
            return f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}?X-Amz-Expires={ExpiresIn}"

        def delete_object(self, Bucket, Key):
            self.calls.append(("delete", Bucket, Key))

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")
    s3 = FakeS3()
    stager = publish.S3Stager("staging-bucket", 1800, client=s3)

    key, url = stager.stage(video)
    stager.discard(key)

    assert key.startswith("instagram/") and key.endswith("/clip.mp4")
    assert url.endswith("X-Amz-Expires=1800")
    assert s3.calls[0][:3] == ("upload", str(video), "staging-bucket")
    assert s3.calls[0][4] == {"ContentType": "video/mp4"}
    assert s3.calls[-1] == ("delete", "staging-bucket", key)
