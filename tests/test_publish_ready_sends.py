"""Offline coverage of ordered publishing and interrupted-request recovery."""

import importlib.util
import hashlib
import json
from pathlib import Path
import sys

import pytest


SPEC = importlib.util.spec_from_file_location(
    "publish_ready_sends", Path(__file__).parents[1] / "tools" / "publish_ready_sends.py"
)
publish = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publish
SPEC.loader.exec_module(publish)


def write(path, records):
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def clip(tmp_path, name="first", taken_at="2025-01-01T12:00:00+00:00"):
    source, output = tmp_path / (name + "-source.mp4"), tmp_path / (name + ".mp4")
    source.write_bytes(b"original video " + name.encode())
    output.write_bytes(b"prepared video " + name.encode())
    return {
        "source_video_path": str(source), "video_path": str(output),
        "climb_name": name, "caption": name + " V7 at 30 degrees",
        "taken_at": taken_at, "status": "approved", "video_outcome": "send",
        "video_reviewed_at": "2025-02-01T00:00:00+00:00", "label_confirmed": True,
        "prepared": True, "title_position": "top", "audio_removed": True,
        "source_sha256": publish.digest(source), "output_sha256": publish.digest(output),
        "cover_frame_ms": 1000,
        "checks": dict.fromkeys([
            "source_unchanged", "dimensions_match", "duration_matches_trim",
            "no_audio", "full_decode_passed",
        ], True),
    }


class FakeMeta:
    user_id = "test-account-id"

    def __init__(self, *, username="testaccount", fail_create=False, fail_publish=False,
                 account_id=None, fail_verification=False, statuses=None):
        self.username = username
        self.account_id = account_id or self.user_id
        self.fail_create = fail_create
        self.fail_publish = fail_publish
        self.fail_verification = fail_verification
        self.statuses = list(statuses or ["FINISHED"])
        self.calls = []
        self.captions = {}
        self.published = {}
        self.recovered_items = []
        self.on_create = None
        self.on_publish = None

    def _call(self, method, path, **fields):
        self.calls.append((method, path))
        if path == "me":
            return {"id": self.account_id, "username": self.username}
        if path.endswith("/media"):
            return {"data": self.recovered_items}
        if self.fail_verification:
            raise RuntimeError("credentials must never be printed: secret-token")
        return {"id": path, "permalink": "https://example.test/reel/" + path,
                "media_product_type": "REELS", "caption": self.published.get(path, ""),
                "timestamp": "2025-02-01T00:00:00+00:00"}

    def create_reel_container(self, url, caption, share_to_feed, **fields):
        self.calls.append(("create", caption, share_to_feed, fields))
        if self.on_create:
            self.on_create()
        if self.fail_create:
            raise RuntimeError("ambiguous request with secret-token and signed URL")
        container_id = "container-" + str(len(self.captions) + 1)
        self.captions[container_id] = caption
        return container_id

    def container_status(self, container_id):
        self.calls.append(("status", container_id))
        status = self.statuses.pop(0) if self.statuses else "FINISHED"
        return status, ""

    def publish(self, container_id):
        self.calls.append(("publish", container_id))
        if self.on_publish:
            self.on_publish()
        if self.fail_publish:
            raise RuntimeError("ambiguous publish response with secret-token")
        media_id = "media-" + str(len(self.published) + 1)
        self.published[media_id] = self.captions.get(container_id, "")
        return media_id


class FakeStager:
    bucket = "test-private-staging"

    def __init__(self, *, fail=False, fail_cleanup=False):
        self.fail = fail
        self.fail_cleanup = fail_cleanup
        self.staged = []
        self.deleted = []

    def stage(self, video):
        if self.fail:
            raise RuntimeError("staging failed with secret-token")
        key = "test/" + video.name
        self.staged.append(key)
        return key, "https://example.test/private?secret-signature"

    def discard(self, key):
        if self.fail_cleanup:
            raise RuntimeError("cleanup failed")
        self.deleted.append(key)


def run(plan, ledger, *extra, meta=None, stager=None, sleep=lambda _: None):
    return publish.main(["--manifest", str(plan), "--ledger", str(ledger),
                         "--wait-seconds", "0", *extra],
                        meta=meta or FakeMeta(), stager=stager or FakeStager(), sleep=sleep)


def attempt(plan, ledger_path, record, stage, **fields):
    ledger = publish.Ledger(ledger_path)
    ledger.start(record, FakeMeta.user_id)
    ledger.update(publish.source_key(record), publication_stage=stage, **fields)
    return ledger


def test_dry_run_orders_dates_without_network_or_files(tmp_path, capsys):
    late = clip(tmp_path, "late", "2025-01-02T00:00:00Z")
    early = clip(tmp_path, "early", "2025-01-01T13:00:00+02:00")
    plan, ledger = write(tmp_path / "plan.jsonl", [late, early]), tmp_path / "journal.jsonl"
    meta, stager = FakeMeta(), FakeStager()
    before = plan.read_bytes()
    assert run(plan, ledger, meta=meta, stager=stager) == 0
    assert capsys.readouterr().out.index("early:") >= 0
    assert not meta.calls and not stager.staged and not ledger.exists()
    assert not ledger.with_suffix(".jsonl.lock").exists()
    assert plan.read_bytes() == before


def test_execute_orders_reels_and_rerun_deduplicates(tmp_path):
    late = clip(tmp_path, "late", "2025-01-02T00:00:00Z")
    early = clip(tmp_path, "early")
    plan, ledger = write(tmp_path / "plan.jsonl", [late, early]), tmp_path / "journal.jsonl"
    before = plan.read_bytes()
    meta, stager = FakeMeta(), FakeStager()
    assert run(plan, ledger, "--execute", meta=meta, stager=stager) == 0
    creates = [call for call in meta.calls if call[0] == "create"]
    assert [call[1] for call in creates] == [early["caption"], late["caption"]]
    assert all(call[2] is True and call[3]["cover_frame_ms"] == 1000 for call in creates)
    entries = publish.Ledger(ledger).records
    assert all(item["publication_verified"] and item["staging_deleted"] for item in entries)
    assert [item["published_media_id"] for item in entries] == ["media-1", "media-2"]
    assert plan.read_bytes() == before
    meta2, stager2 = FakeMeta(), FakeStager()
    assert run(plan, ledger, "--execute", meta=meta2, stager=stager2) == 0
    assert meta2.calls == [("GET", "me")] and not stager2.staged


def test_requests_are_durable_before_network_and_id_before_cleanup(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    meta = FakeMeta()
    meta.on_create = lambda: assert_stage(path, "container_requested")
    meta.on_publish = lambda: assert_stage(path, "publish_requested")
    assert run(plan, path, "--execute", meta=meta, stager=FakeStager(fail_cleanup=True)) == 1
    entry = publish.Ledger(path).records[0]
    assert entry["published_media_id"] == "media-1" and entry["status"] == "published"
    stager = FakeStager()
    assert run(plan, path, "--execute", meta=meta, stager=stager) == 0
    assert len([call for call in meta.calls if call[0] == "publish"]) == 1
    assert stager.deleted == [entry["staging_key"]]


def assert_stage(path, expected):
    assert publish.Ledger(path).records[0]["publication_stage"] == expected


def test_staging_failure_can_retry_without_remote_ambiguity(tmp_path, capsys):
    record = clip(tmp_path)
    plan, ledger = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    meta = FakeMeta()
    assert run(plan, ledger, "--execute", meta=meta, stager=FakeStager(fail=True)) == 1
    assert_stage(ledger, "before_staging")
    assert run(plan, ledger, "--execute", meta=meta, stager=FakeStager()) == 0
    assert len([call for call in meta.calls if call[0] == "create"]) == 1
    assert "secret-token" not in capsys.readouterr().err
    assert "secret-token" not in ledger.read_text()


def test_ambiguous_container_creation_does_not_repost(tmp_path):
    record = clip(tmp_path)
    plan, ledger = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    meta, stager = FakeMeta(fail_create=True), FakeStager()
    assert run(plan, ledger, "--execute", meta=meta, stager=stager) == 1
    assert_stage(ledger, "container_requested")
    other = FakeMeta()
    assert run(plan, ledger, "--execute", meta=other, stager=stager) == 1
    assert not any(call[0] in {"create", "publish"} for call in other.calls)
    # Meta may still be downloading; the pending object remains lifecycle protected.
    assert not stager.deleted


def test_ambiguous_publish_waits_for_reconciliation_even_if_container_finished(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    meta = FakeMeta(fail_publish=True)
    assert run(plan, path, "--execute", meta=meta) == 1
    assert_stage(path, "publish_requested")
    next_meta = FakeMeta()
    assert run(plan, path, "--execute", meta=next_meta) == 1
    assert [call[0] for call in next_meta.calls] == ["GET", "status"]


def test_recover_finished_container_without_new_upload(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    attempt(plan, path, record, "container_created", container_id="old-container",
            staging_key="old-staging", staging_bucket=FakeStager.bucket)
    meta, stager = FakeMeta(), FakeStager()
    meta.captions["old-container"] = record["caption"]
    assert run(plan, path, "--execute", meta=meta, stager=stager) == 0
    assert not stager.staged and stager.deleted == ["old-staging"]
    assert not any(call[0] == "create" for call in meta.calls)
    assert ("publish", "old-container") in meta.calls


def test_recover_already_published_container_without_second_publish(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    ledger = attempt(plan, path, record, "publish_requested", container_id="old-container")
    meta = FakeMeta(statuses=["PUBLISHED"])
    started = ledger.records[0]["publishing_started_at"]
    meta.recovered_items = [{"id": "existing-media", "caption": record["caption"],
                             "media_product_type": "REELS", "timestamp": started}]
    meta.published["existing-media"] = record["caption"]
    assert run(plan, path, "--execute", meta=meta) == 0
    assert not any(call[0] in {"create", "publish"} for call in meta.calls)
    assert publish.Ledger(path).records[0]["published_media_id"] == "existing-media"


def test_multiple_recovery_candidates_stop_safely(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    ledger = attempt(plan, path, record, "publish_requested", container_id="old-container")
    meta = FakeMeta(statuses=["PUBLISHED"])
    meta.recovered_items = [{"id": media_id, "caption": record["caption"],
                             "media_product_type": "REELS", "timestamp": ledger.records[0]["publishing_started_at"]}
                            for media_id in ["one", "two"]]
    assert run(plan, path, "--execute", meta=meta) == 1
    assert not any(call[0] in {"create", "publish"} for call in meta.calls)


@pytest.mark.parametrize("field,value", [
    ("video_outcome", "fall"), ("status", "needs_review"), ("prepared", False),
    ("title_position", "bottom"), ("audio_removed", False), ("video_reviewed_at", None),
    ("label_confirmed", False), ("source_sha256", "0" * 64), ("output_sha256", "0" * 64),
    ("wrong_video", True), ("needs_label_confirmation", True),
])
def test_unverified_or_changed_clips_do_not_publish(tmp_path, field, value):
    record = clip(tmp_path)
    record[field] = value
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    meta, stager = FakeMeta(), FakeStager()
    expected = 1 if publish.eligible(record) else 0
    assert run(plan, path, "--execute", meta=meta, stager=stager) == expected
    assert not stager.staged and not path.exists()
    assert not any(call[0] == "create" for call in meta.calls)


def test_description_match_qualifies_but_false_confirmation_overrides(tmp_path):
    record = clip(tmp_path)
    record.pop("label_confirmed")
    record["match_method"] = "description"
    publish.verify_prepared(record)
    record["label_confirmed"] = False
    with pytest.raises(publish.BatchStop, match="label"):
        publish.verify_prepared(record)


def test_oldest_unprepared_clip_blocks_newer_ready_clips(tmp_path):
    early, late = clip(tmp_path), clip(tmp_path, "later", "2025-02-01T00:00:00Z")
    early["prepared"] = False
    plan, path = write(tmp_path / "plan.jsonl", [late, early]), tmp_path / "journal.jsonl"
    meta, stager = FakeMeta(), FakeStager()
    assert run(plan, path, "--execute", meta=meta, stager=stager) == 1
    assert not stager.staged and not path.exists()


def test_failed_preparation_check_blocks_upload(tmp_path):
    record = clip(tmp_path)
    record["checks"]["full_decode_passed"] = False
    with pytest.raises(publish.BatchStop, match="checks"):
        publish.verify_prepared(record)


@pytest.mark.parametrize("options", [{"username": "wrong"}, {"account_id": "wrong-id"}])
def test_account_guard_prevents_any_writes(tmp_path, options):
    plan = write(tmp_path / "plan.jsonl", [clip(tmp_path)])
    path, stager = tmp_path / "journal.jsonl", FakeStager()
    assert run(plan, path, "--execute", "--expected-username", "testaccount",
               meta=FakeMeta(**options), stager=stager) == 1
    assert not path.exists() and not stager.staged


def test_journal_account_mismatch_blocks_resume(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    ledger = attempt(plan, path, record, "before_staging")
    ledger.update(publish.source_key(record), instagram_user_id="other-account")
    stager = FakeStager()
    assert run(plan, path, "--execute", stager=stager) == 1
    assert not stager.staged


def test_lock_prevents_concurrent_publishers_and_releases(tmp_path):
    path = tmp_path / "journal.jsonl"
    with publish.publisher_lock(path):
        with pytest.raises(publish.BatchStop, match="another publisher"):
            with publish.publisher_lock(path):
                pass
    with publish.publisher_lock(path):
        pass


def test_duplicate_sources_or_media_ids_are_rejected(tmp_path):
    first, second = clip(tmp_path), clip(tmp_path, "other")
    path = write(tmp_path / "plan.jsonl", [first, dict(first)])
    with pytest.raises(publish.BatchStop, match="duplicate sources"):
        publish.plan_records(path)
    first["published_media_id"] = second["published_media_id"] = "same-id"
    write(path, [first, second])
    with pytest.raises(publish.BatchStop, match="duplicate"):
        publish.Ledger(path)


def test_limit_counts_new_posts_and_retained_sources_are_skipped(tmp_path):
    kept = clip(tmp_path, "kept")
    kept.update(status="published", published_media_id="kept-id")
    next_clip = clip(tmp_path, "next", "2025-02-01T00:00:00Z")
    last = clip(tmp_path, "last", "2025-03-01T00:00:00Z")
    plan, ledger = write(tmp_path / "plan.jsonl", [last, kept, next_clip]), tmp_path / "journal.jsonl"
    meta = FakeMeta()
    assert run(plan, ledger, "--execute", "--limit", "1", meta=meta) == 0
    assert [call[1] for call in meta.calls if call[0] == "create"] == [next_clip["caption"]]


def test_relative_paths_are_manifest_relative(tmp_path):
    record = clip(tmp_path)
    record["source_video_path"] = Path(record["source_video_path"]).name
    record["video_path"] = Path(record["video_path"]).name
    plan = write(tmp_path / "plan.jsonl", [record])
    normalized = publish.plan_records(plan)[0]
    assert Path(normalized["video_path"]) == tmp_path / "first.mp4"
    publish.verify_prepared(normalized)


def test_plan_and_journal_cannot_be_the_same_file(tmp_path):
    plan = write(tmp_path / "plan.jsonl", [clip(tmp_path)])
    with pytest.raises(SystemExit) as error:
        run(plan, plan, "--execute")
    assert error.value.code == 2


def test_naive_capture_date_is_rejected(tmp_path):
    record = clip(tmp_path, taken_at="2025-01-01T12:00:00")
    with pytest.raises(publish.BatchStop, match="timezone"):
        publish.plan_records(write(tmp_path / "plan.jsonl", [record]))


def test_preparation_wait_rereads_an_atomically_updated_plan(tmp_path):
    record = clip(tmp_path)
    record["prepared"] = False
    plan = write(tmp_path / "plan.jsonl", [record])
    ticks = iter([0, 0])

    def complete_render(_):
        record["prepared"] = True
        replacement = write(tmp_path / "replacement.jsonl", [record])
        replacement.replace(plan)

    ready = publish.prepared_record(plan, publish.source_key(record), 10, 1,
                                    sleep=complete_render, clock=lambda: next(ticks))
    assert ready["prepared"] is True


def test_failed_processing_cleanup_can_resume_without_new_container(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    meta, stager = FakeMeta(statuses=["ERROR"]), FakeStager()
    assert run(plan, path, "--execute", meta=meta, stager=stager) == 1
    # The known container remains journaled so the failure cannot trigger a repost.
    assert_stage(path, "container_created")
    next_meta = FakeMeta(statuses=["ERROR"])
    assert run(plan, path, "--execute", meta=next_meta, stager=stager) == 1
    assert stager.deleted == stager.staged
    assert not any(call[0] in {"create", "publish"} for call in next_meta.calls)


def test_public_verification_failure_preserves_id_and_never_reposts(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    meta = FakeMeta(fail_verification=True)
    assert run(plan, path, "--execute", meta=meta) == 1
    entry = publish.Ledger(path).records[0]
    assert entry["published_media_id"] == "media-1" and not entry.get("publication_verified")
    meta.fail_verification = False
    assert run(plan, path, "--execute", meta=meta) == 0
    assert sum(call[0] == "publish" for call in meta.calls) == 1
    assert publish.Ledger(path).records[0]["publication_verified"] is True


def test_unresolved_journal_source_cannot_be_removed_to_skip_ahead(tmp_path):
    old, new = clip(tmp_path), clip(tmp_path, "new", "2025-02-01T00:00:00Z")
    plan, path = write(tmp_path / "plan.jsonl", [new]), tmp_path / "journal.jsonl"
    attempt(plan, path, old, "container_requested")
    meta, stager = FakeMeta(), FakeStager()
    assert run(plan, path, "--execute", meta=meta, stager=stager) == 1
    assert meta.calls == [("GET", "me")] and not stager.staged


def test_safe_staged_retry_cleans_previous_object_before_upload(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    attempt(plan, path, record, "staged", staging_key="previous-object", staging_bucket=FakeStager.bucket)
    stager = FakeStager()
    assert run(plan, path, "--execute", stager=stager) == 0
    assert stager.deleted[0] == "previous-object"
    assert len(stager.staged) == 1


def test_pending_cleanup_requires_the_original_staging_bucket(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    attempt(plan, path, record, "staged", staging_key="previous-object", staging_bucket="other-bucket")
    meta, stager = FakeMeta(), FakeStager()
    assert run(plan, path, "--execute", meta=meta, stager=stager) == 1
    assert not stager.deleted and not stager.staged
    assert meta.calls == [("GET", "me")]


def test_stream_hash_supports_files_larger_than_a_chunk(tmp_path):
    contents = b"send" * 300000
    source = tmp_path / "large.bin"
    source.write_bytes(contents)
    assert publish.digest(source) == hashlib.sha256(contents).hexdigest()


def test_wait_supports_original_only_video_path_before_rendering(tmp_path):
    ready = clip(tmp_path)
    original = dict(ready, video_path=ready["source_video_path"], prepared=False)
    original.pop("source_video_path")
    plan = write(tmp_path / "plan.jsonl", [original])
    ticks = iter([0, 0])

    def complete_render(_):
        write(plan, [ready])

    prepared = publish.prepared_record(plan, publish.source_key(original), 10, 1,
                                       sleep=complete_render, clock=lambda: next(ticks))
    assert publish.source_key(prepared) == publish.source_key(original)
    publish.verify_prepared(prepared)


def test_mixed_manifest_skips_falls_uncertain_and_unapproved_clips(tmp_path):
    fall = clip(tmp_path, "fall", "invalid date never enters the queue")
    fall["video_outcome"] = "fall"
    uncertain = clip(tmp_path, "uncertain")
    uncertain["video_outcome"] = "uncertain"
    unapproved = clip(tmp_path, "unapproved")
    unapproved["status"] = "needs_review"
    unconfirmed = clip(tmp_path, "unconfirmed")
    unconfirmed["label_confirmed"] = False
    valid = clip(tmp_path, "ready", "2025-02-01T00:00:00Z")
    plan, path = write(tmp_path / "plan.jsonl", [fall, uncertain, unapproved, unconfirmed, valid]), tmp_path / "journal.jsonl"
    meta = FakeMeta()
    assert run(plan, path, "--execute", meta=meta) == 0
    assert [call[1] for call in meta.calls if call[0] == "create"] == [valid["caption"]]


@pytest.mark.parametrize("change", ["caption", "output", "source", "revoked"])
def test_pending_container_cannot_publish_changed_or_withdrawn_plan(tmp_path, change):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    attempt(plan, path, record, "container_created", container_id="pending-container")
    if change == "caption":
        record["caption"] = "changed caption"
    elif change == "revoked":
        record["label_confirmed"] = False
    else:
        file_field, hash_field = ("video_path", "output_sha256") if change == "output" else ("source_video_path", "source_sha256")
        changed = Path(record[file_field])
        changed.write_bytes(b"changed video")
        record[hash_field] = publish.digest(changed)
    write(plan, [record])
    meta, stager = FakeMeta(), FakeStager()
    assert run(plan, path, "--execute", meta=meta, stager=stager) == 1
    assert not any(call[0] in {"create", "publish"} for call in meta.calls)
    assert not stager.staged


def test_withdrawn_safe_staging_attempt_cannot_be_silently_skipped(tmp_path):
    old, newer = clip(tmp_path), clip(tmp_path, "newer", "2025-02-01T00:00:00Z")
    plan, path = write(tmp_path / "plan.jsonl", [old, newer]), tmp_path / "journal.jsonl"
    attempt(plan, path, old, "before_staging")
    old["status"] = "needs_review"
    write(plan, [old, newer])
    meta = FakeMeta()
    assert run(plan, path, "--execute", meta=meta) == 1
    assert not any(call[0] in {"create", "publish"} for call in meta.calls)


def test_withdrawn_ambiguous_publish_only_reconciles_an_already_public_container(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    ledger = attempt(plan, path, record, "publish_requested", container_id="pending-container")
    record["label_confirmed"] = False
    write(plan, [record])
    finished = FakeMeta(statuses=["FINISHED"])
    assert run(plan, path, "--execute", meta=finished) == 1
    assert not any(call[0] in {"create", "publish"} for call in finished.calls)
    public = FakeMeta(statuses=["PUBLISHED"])
    public.recovered_items = [{"id": "existing-media", "caption": record["caption"],
                               "media_product_type": "REELS", "timestamp": ledger.records[0]["publishing_started_at"]}]
    public.published["existing-media"] = record["caption"]
    assert run(plan, path, "--execute", meta=public) == 0
    assert not any(call[0] in {"create", "publish"} for call in public.calls)
    assert publish.Ledger(path).records[0]["published_media_id"] == "existing-media"


@pytest.mark.parametrize("resume", [False, True])
def test_review_withdrawn_during_processing_never_sends_publish_request(tmp_path, resume):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    if resume:
        attempt(plan, path, record, "container_created", container_id="pending-container")
    statuses = ["IN_PROGRESS", "IN_PROGRESS", "FINISHED"] if resume else ["IN_PROGRESS", "FINISHED"]
    meta, stager = FakeMeta(statuses=statuses), FakeStager()

    def withdraw_review(_):
        record["video_outcome"] = "fall"
        write(plan, [record])

    assert run(plan, path, "--execute", meta=meta, stager=stager, sleep=withdraw_review) == 1
    assert not any(call[0] == "publish" for call in meta.calls)
    entry = publish.Ledger(path).records[0]
    assert entry["container_id"] and entry["publication_stage"] == "container_created"
    assert not entry.get("published_media_id")


def test_plan_marked_published_cannot_hide_unresolved_attempt(tmp_path):
    record = clip(tmp_path)
    newer = clip(tmp_path, "newer", "2025-02-01T00:00:00Z")
    plan, path = write(tmp_path / "plan.jsonl", [record, newer]), tmp_path / "journal.jsonl"
    attempt(plan, path, record, "publish_requested", container_id="pending-container")
    record.update(status="published", published_media_id="claimed-public-id")
    write(plan, [record, newer])
    meta = FakeMeta()
    assert run(plan, path, "--execute", meta=meta) == 1
    assert not any(call[0] in {"create", "publish"} for call in meta.calls)


def test_published_plan_still_finishes_journal_verification_and_cleanup(tmp_path):
    record = clip(tmp_path)
    plan, path = write(tmp_path / "plan.jsonl", [record]), tmp_path / "journal.jsonl"
    meta = FakeMeta()
    assert run(plan, path, "--execute", meta=meta, stager=FakeStager(fail_cleanup=True)) == 1
    record.update(status="published", published_media_id="media-1")
    write(plan, [record])
    stager = FakeStager()
    assert run(plan, path, "--execute", meta=meta, stager=stager) == 0
    assert stager.deleted and publish.Ledger(path).records[0]["publication_verified"]
    assert sum(call[0] == "publish" for call in meta.calls) == 1
