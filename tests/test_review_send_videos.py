import importlib.util
import json
from pathlib import Path
import shutil
import sys
import subprocess

import pytest


MODULE_PATH = Path(__file__).parents[1] / "tools" / "review_send_videos.py"
SPEC = importlib.util.spec_from_file_location("review_send_videos", MODULE_PATH)
review = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = review
SPEC.loader.exec_module(review)


def test_logbook_hint_is_context_not_outcome():
    assert review._logbook_hint({"send": "Sent in 3 tries"}) == "Logbook hint: send (Sent in 3 tries)"
    assert review._logbook_hint({"send": "Project · 2 tries so far"}) == "Logbook hint: try (Project · 2 tries so far)"
    assert review._logbook_hint(None) == "No manifest match"


def test_create_review_page_makes_sheets_html_and_json(monkeypatch, tmp_path):
    source_dir = tmp_path / "videos"
    source_dir.mkdir()
    first = source_dir / "first clip.mp4"
    second = source_dir / "second.mp4"
    first.write_bytes(b"video")
    second.write_bytes(b"video")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"video_path": str(first.resolve()), "status": "ready", "send": "Sent in 2 tries", "climb_name": "Example"}) + "\n", encoding="utf-8")

    monkeypatch.setattr(review, "probe_video", lambda path: review.VideoInfo(640, 360, 4.0, 30, False))

    def fake_sheet(video, output, interval, columns):
        output.write_bytes(b"jpeg")

    monkeypatch.setattr(review, "make_sheet", fake_sheet)
    output = tmp_path / "review" / "index.html"
    records = review.create_review_page(source_dir, output, interval=0.5, columns=2, manifest=manifest)

    assert len(records) == 2
    assert records[0]["outcome"] == "uncertain"
    assert records[0]["logbook_hint"] == "Logbook hint: send (Sent in 2 tries)"
    assert output.is_file()
    assert output.with_suffix(".json").is_file()
    assert len(list(output.with_suffix("").glob("*.jpg"))) == 2
    page = output.read_text(encoding="utf-8")
    assert "Download review JSON" in page
    assert "Fall / try" in page
    assert "Logbook hint: send" in page
    assert "first%20clip.mp4" in page


def test_apply_reviews_updates_review_fields_but_not_status_by_default(tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"video_path": str(video), "status": "ready"}) + "\n", encoding="utf-8")
    review_json = tmp_path / "review.json"
    review_json.write_text(json.dumps({"reviews": [{"id": "1", "video_path": str(video), "outcome": "send", "start_seconds": 1.2, "end_seconds": 5.0, "notes": "held finish", "reviewed_at": "2026-10-02T12:00:00+00:00"}]}), encoding="utf-8")

    assert review.apply_reviews(review_json, manifest=manifest) == 1
    record = json.loads(manifest.read_text(encoding="utf-8").strip())
    assert record["video_outcome"] == "send"
    assert record["start_seconds"] == 1.2
    assert record["status"] == "ready"
    assert "statuses were left unchanged" in capsys.readouterr().out


def test_apply_reviews_can_explicitly_approve_sends_and_skip_falls(tmp_path):
    send = tmp_path / "send.mp4"
    fall = tmp_path / "fall.mp4"
    send.write_bytes(b"video")
    fall.write_bytes(b"video")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("\n".join([
        json.dumps({"video_path": str(send), "status": "ready"}),
        json.dumps({"video_path": str(fall), "status": "check_climb"}),
    ]) + "\n", encoding="utf-8")
    review_json = tmp_path / "review.json"
    review_json.write_text(json.dumps({"reviews": [
        {"video_path": str(send), "outcome": "send", "start_seconds": 1.0,
         "reviewed_at": "2026-10-02T12:00:00+00:00", "label_confirmed": True},
        {"video_path": str(fall), "outcome": "fall", "reviewed_at": "2026-10-02T12:00:00+00:00"},
    ]}), encoding="utf-8")

    assert review.apply_reviews(review_json, manifest=manifest, approve_sends=True, skip_falls=True) == 2
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert [record["status"] for record in records] == ["approved", "skip"]


def test_apply_reviews_rejects_unknown_outcome(tmp_path):
    review_json = tmp_path / "review.json"
    review_json.write_text(json.dumps({"reviews": [{"video_path": "clip.mp4", "outcome": "maybe"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported outcome"):
        review.apply_reviews(review_json)


def test_review_module_imports_without_prepare_imported_first():
    command = (
        "import importlib.util, sys; "
        f"spec=importlib.util.spec_from_file_location('isolated_review', {str(MODULE_PATH)!r}); "
        "module=importlib.util.module_from_spec(spec); "
        "sys.modules[spec.name]=module; spec.loader.exec_module(module)"
    )
    subprocess.run([sys.executable, "-c", command], check=True, capture_output=True, text=True)


def write_review(tmp_path, reviews):
    path = tmp_path / "review.json"
    path.write_text(json.dumps({"reviews": reviews}), encoding="utf-8")
    return path


def confirmed_review(video, **changes):
    return {
        "video_path": str(video), "outcome": "send", "start_seconds": 1.0,
        "end_seconds": 4.0, "duration_seconds": 5.0, "label_confirmed": True,
        "notes": "Held the finish", "reviewed_at": "2026-10-02T12:00:00+00:00",
        **changes,
    }


@pytest.mark.parametrize("changes, message", [
    ({"start_seconds": float("nan")}, "finite"),
    ({"start_seconds": float("inf")}, "finite"),
    ({"start_seconds": -1}, "non-negative"),
    ({"start_seconds": "1"}, "number"),
    ({"start_seconds": True}, "number"),
    ({"end_seconds": 1}, "before"),
    ({"end_seconds": 6}, "duration_seconds"),
    ({"duration_seconds": 0}, "duration_seconds"),
    ({"reviewed_at": None}, "actual review"),
    ({"reviewed_at": "yesterday"}, "valid ISO"),
    ({"reviewed_at": "2026-10-02T12:00:00"}, "timezone"),
    ({"label_confirmed": "yes"}, "boolean"),
    ({"notes": []}, "text"),
    ({"video_path": ""}, "video_path"),
])
def test_invalid_reviews_are_rejected_before_manifest_writes(tmp_path, changes, message):
    first, second = tmp_path / "first.mp4", tmp_path / "second.mp4"
    manifest = tmp_path / "manifest.jsonl"
    original = "\n".join(json.dumps({"video_path": str(path), "status": "ready"}) for path in (first, second)) + "\n"
    manifest.write_text(original, encoding="utf-8")
    handoff = write_review(tmp_path, [confirmed_review(first), confirmed_review(second, **changes)])
    with pytest.raises(ValueError, match=message):
        review.apply_reviews(handoff, manifest=manifest, approve_sends=True)
    assert manifest.read_text(encoding="utf-8") == original


def test_review_rejects_duplicate_paths_ids_and_non_objects(tmp_path):
    video = tmp_path / "clip.mp4"
    for reviews, message in [
        ([confirmed_review(video), confirmed_review(video)], "duplicate review"),
        ([confirmed_review(video, id="same"), confirmed_review(tmp_path / "other.mp4", id="same")], "IDs"),
        (["not an object"], "every review must be an object"),
    ]:
        with pytest.raises(ValueError, match=message):
            review.apply_reviews(write_review(tmp_path, reviews))


def test_untouched_uncertain_record_is_never_marked_reviewed(tmp_path):
    video = tmp_path / "clip.mp4"
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"video_path": str(video), "status": "ready"}) + "\n", encoding="utf-8")
    handoff = write_review(tmp_path, [{"video_path": str(video), "outcome": "uncertain", "reviewed_at": None}])
    review.apply_reviews(handoff, manifest=manifest)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    assert record["video_reviewed_at"] is None
    assert record["video_outcome"] == "uncertain"
    assert record["status"] == "ready"


@pytest.mark.parametrize("outcome", ["send", "fall"])
def test_review_preserves_published_status_and_ids(tmp_path, outcome):
    video = tmp_path / "clip.mp4"
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"video_path": str(video), "status": "published", "published_media_id": "existing", "container_id": "container"}) + "\n", encoding="utf-8")
    review.apply_reviews(write_review(tmp_path, [confirmed_review(video, outcome=outcome)]),
                         manifest=manifest, approve_sends=True, skip_falls=True)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    assert record["status"] == "published"
    assert record["published_media_id"] == "existing"
    assert record["container_id"] == "container"
    assert record["video_outcome"] == outcome


def test_send_review_requires_explicit_label_confirmation_for_unresolved_label(tmp_path):
    video = tmp_path / "clip.mp4"
    manifest = tmp_path / "manifest.jsonl"
    original = json.dumps({"video_path": str(video), "status": "check_climb", "match_method": "time", "needs_label_confirmation": True}) + "\n"
    manifest.write_text(original, encoding="utf-8")
    handoff = write_review(tmp_path, [confirmed_review(video, label_confirmed=False)])
    with pytest.raises(ValueError, match="confirm the climb label"):
        review.apply_reviews(handoff, manifest=manifest, approve_sends=True)
    assert manifest.read_text(encoding="utf-8") == original
    review.apply_reviews(write_review(tmp_path, [confirmed_review(video)]), manifest=manifest, approve_sends=True)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    assert record["status"] == "approved"
    assert record["label_confirmed"] is True
    assert record["needs_label_confirmation"] is False


def test_explicit_label_confirmation_does_not_clear_wrong_video(tmp_path):
    video = tmp_path / "clip.mp4"
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"video_path": str(video), "status": "check_climb", "wrong_video": True,
                                    "needs_label_confirmation": True}) + "\n", encoding="utf-8")
    review.apply_reviews(write_review(tmp_path, [confirmed_review(video)]), manifest=manifest)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    assert record["needs_label_confirmation"] is False
    assert record["wrong_video"] is True


def test_unconfirmed_label_keeps_its_confirmation_flag(tmp_path):
    video = tmp_path / "clip.mp4"
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"video_path": str(video), "status": "check_climb", "needs_label_confirmation": True}) + "\n", encoding="utf-8")
    review.apply_reviews(write_review(tmp_path, [confirmed_review(video, label_confirmed=False)]), manifest=manifest)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    assert record["needs_label_confirmation"] is True


def test_regenerated_review_keeps_rejected_description_match_unconfirmed(monkeypatch, tmp_path):
    source_dir = tmp_path / "videos"
    source_dir.mkdir()
    video = source_dir / "clip.mp4"
    video.write_bytes(b"video")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"video_path": str(video), "status": "ready",
                                    "match_method": "description", "label_confirmed": False}) + "\n", encoding="utf-8")
    monkeypatch.setattr(review, "probe_video", lambda path: review.VideoInfo(640, 360, 5, 30, False))
    records = review.create_review_page(source_dir, tmp_path / "review.html", manifest=manifest, sheets=False)
    assert records[0]["label_confirmed"] is False
    assert records[0]["label_rejected"] is True
    generated = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))["reviews"][0]
    assert generated["label_confirmed"] is False


@pytest.mark.parametrize("approve", [False, True])
def test_apply_without_explicit_confirmation_preserves_rejected_description_match(tmp_path, approve):
    video = tmp_path / "clip.mp4"
    manifest = tmp_path / "manifest.jsonl"
    original = json.dumps({"video_path": str(video), "status": "ready", "match_method": "description",
                           "label_confirmed": False, "needs_label_confirmation": True}) + "\n"
    manifest.write_text(original, encoding="utf-8")
    decision = confirmed_review(video)
    decision.pop("label_confirmed")
    handoff = write_review(tmp_path, [decision])
    if approve:
        with pytest.raises(ValueError, match="confirm the climb label"):
            review.apply_reviews(handoff, manifest=manifest, approve_sends=True)
        assert manifest.read_text(encoding="utf-8") == original
    else:
        review.apply_reviews(handoff, manifest=manifest)
        record = json.loads(manifest.read_text(encoding="utf-8"))
        assert record["label_confirmed"] is False
        assert record["needs_label_confirmation"] is True
        assert record["status"] == "ready"


def test_explicit_handoff_can_reconfirm_a_rejected_description_match(tmp_path):
    video = tmp_path / "clip.mp4"
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({"video_path": str(video), "status": "ready", "match_method": "description",
                                    "label_confirmed": False, "needs_label_confirmation": True}) + "\n", encoding="utf-8")
    review.apply_reviews(write_review(tmp_path, [confirmed_review(video)]), manifest=manifest, approve_sends=True)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    assert record["label_confirmed"] is True
    assert record["needs_label_confirmation"] is False
    assert record["status"] == "approved"


def test_apply_matches_prepared_source_and_resolves_relative_paths_at_each_json(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    manifest = data / "manifest.jsonl"
    manifest.write_text(json.dumps({"source_video_path": "../videos/original.mp4", "video_path": "../outputs/edited.mp4", "status": "ready"}) + "\n", encoding="utf-8")
    handoff = write_review(reviews, [confirmed_review("../videos/original.mp4")])
    assert review.apply_reviews(handoff, manifest=manifest, approve_sends=True) == 1
    record = json.loads(manifest.read_text(encoding="utf-8"))
    assert record["source_video_path"] == "../videos/original.mp4"
    assert record["video_path"] == "../outputs/edited.mp4"
    assert record["status"] == "approved"


def test_source_identity_changes_with_source_path_or_file_changes(monkeypatch, tmp_path):
    first = tmp_path / "one" / "clip.mp4"
    second = tmp_path / "two" / "clip.mp4"
    for path in (first, second):
        path.parent.mkdir()
        path.write_bytes(b"video")
    monkeypatch.setattr(review, "probe_video", lambda path: review.VideoInfo(640, 360, 5, 30, False))
    record = review._review_record(1, first, None, {"match_method": "description", "status": "ready"}, tmp_path / "index.html")
    other = review._review_record(1, second, None, {"match_method": "time", "status": "check_climb"}, tmp_path / "index.html")
    assert record["id"] != other["id"]
    assert record["label_confirmed"] is True
    assert other["label_confirmed"] is False
    page = review._page([record, other], "2026-10-02T12:00:00+00:00")
    assert "saved.video_path !== record.video_path" in page
    assert "saved.source_identity !== record.source_identity" in page
    assert "for (const field of DECISION_FIELDS)" in page
    assert "Object.assign(state" not in page
    first.write_bytes(b"changed video contents")
    changed = review._review_record(1, first, None, None, tmp_path / "index.html")
    assert record["id"] != changed["id"]
    handoff = write_review(tmp_path, [confirmed_review(first, source_identity=record["source_identity"])])
    with pytest.raises(ValueError, match="source video changed"):
        review.apply_reviews(handoff)


def test_manifest_source_duration_cannot_be_bypassed_by_review_duration(tmp_path):
    video = tmp_path / "clip.mp4"
    manifest = tmp_path / "manifest.jsonl"
    original = json.dumps({"video_path": str(video), "status": "ready", "source_duration_seconds": 3.0}) + "\n"
    manifest.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="manifest source duration"):
        review.apply_reviews(write_review(tmp_path, [confirmed_review(video)]), manifest=manifest)
    assert manifest.read_text(encoding="utf-8") == original


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for the browser-state behavior check")
def test_browser_state_only_imports_decisions_for_the_same_source(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(review, "probe_video", lambda path: review.VideoInfo(640, 360, 5, 30, False))
    record = review._review_record(1, source, None, None, tmp_path / "index.html")
    page = review._page([record], "2026-10-02T12:00:00+00:00")
    state_script = page.split("<script>", 1)[1].split("\ntry {", 1)[0]
    checks = """
const assert = require('assert').strict;
const original = RECORDS[0];
const saved = { ...original, outcome: 'send', start_seconds: 1, end_seconds: 4,
  notes: 'Held finish', reviewed_at: '2026-10-02T12:00:00+00:00', label_confirmed: true };
mergeDecision({ ...saved, source_identity: 'another-file' });
assert.equal(state[original.id].outcome, 'uncertain');
mergeDecision({ ...saved, video_path: 'another-directory/clip.mp4' });
assert.equal(state[original.id].outcome, 'uncertain');
mergeDecision({ ...saved, reviewed_at: null });
assert.equal(state[original.id].outcome, 'uncertain');
mergeDecision({ ...saved, start_seconds: NaN });
assert.equal(state[original.id].outcome, 'uncertain');
mergeDecision({ ...saved, caption: 'untrusted replacement', video_href: 'https://elsewhere/' });
assert.equal(state[original.id].outcome, 'send');
assert.equal(state[original.id].notes, 'Held finish');
assert.equal(state[original.id].label_confirmed, true);
assert.equal(state[original.id].caption, original.caption);
assert.equal(state[original.id].video_href, original.video_href);
assert.equal(state[original.id].video_path, original.video_path);
"""
    subprocess.run([shutil.which("node"), "-"], input=state_script + checks,
                   check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is optional for the browser-state behavior check")
def test_browser_rejection_survives_saved_state_and_import_until_checkbox_reconfirmation(monkeypatch, tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(review, "probe_video", lambda path: review.VideoInfo(640, 360, 5, 30, False))
    record = review._review_record(1, source, None,
                                  {"status": "ready", "match_method": "description", "label_confirmed": False},
                                  tmp_path / "index.html")
    page = review._page([record], "2026-10-02T12:00:00+00:00")
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    setup = "const currentRecord = " + json.dumps(record) + ";\n" + """
const assert = require('assert').strict;
const savedDecision = { ...currentRecord, outcome: 'send', start_seconds: 1, end_seconds: 4,
  notes: 'Previously reviewed', reviewed_at: '2026-10-02T12:00:00+00:00',
  label_confirmed: true, label_rejected: false };
let persisted = null;
const localStorage = {
  getItem: () => JSON.stringify({ [currentRecord.id]: savedDecision }),
  setItem: (key, value) => { persisted = JSON.parse(value); }
};
const inputHandlers = {};
const checkbox = {
  type: 'checkbox', dataset: { field: 'label_confirmed' }, checked: false,
  addEventListener: (event, handler) => { inputHandlers[event] = handler; },
  closest: () => ({ dataset: { id: currentRecord.id } })
};
const card = { dataset: { id: currentRecord.id },
  querySelectorAll: selector => selector === '[data-field]' ? [checkbox] : [] };
const elements = {};
const document = {
  querySelectorAll: selector => selector === '[data-field]' ? [checkbox]
    : selector === '.clip-card' ? [card] : [],
  getElementById: id => elements[id] || (elements[id] = {
    handlers: {}, addEventListener(event, handler) { this.handlers[event] = handler; }
  })
};
class FileReader {
  readAsText(file) { this.result = JSON.stringify(file.payload); this.onload(); }
}
function alert(message) { throw new Error(message); }
"""
    checks = """
assert.equal(state[currentRecord.id].outcome, 'send');
assert.equal(state[currentRecord.id].label_rejected, true);
assert.equal(state[currentRecord.id].label_confirmed, false);
assert.equal(checkbox.checked, false);
elements.import.handlers.change({ target: { files: [{ payload: { reviews: [savedDecision] } }] } });
assert.equal(state[currentRecord.id].label_confirmed, false);
assert.equal(checkbox.checked, false);
checkbox.checked = true;
inputHandlers.input();
assert.equal(state[currentRecord.id].label_confirmed, true);
assert.equal(persisted[currentRecord.id].label_confirmed, true);
"""
    subprocess.run([shutil.which("node"), "-"], input=setup + script + checks,
                   check=True, capture_output=True, text=True)
