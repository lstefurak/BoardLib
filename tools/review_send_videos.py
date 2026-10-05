#!/usr/bin/env python3
"""Create a local, review-first send/fall video review page.

The page deliberately keeps the final climbing outcome human-confirmed.  The
logbook match is shown as a hint, but it is not treated as proof that the
video contains a send.  Review decisions can be downloaded as JSON and later
applied to the Instagram manifest with an explicit command.

FFmpeg and ffprobe are required through ``prepare_send_video.py``.  No network
request, model, or third-party upload is made by this tool.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable
from urllib.parse import quote

try:
    from prepare_send_video import VideoError, VideoInfo, make_sheet, probe_video
except ModuleNotFoundError:  # pragma: no cover - useful when imported by a test runner
    import importlib.util

    _prepare_path = Path(__file__).with_name("prepare_send_video.py")
    _prepare_spec = importlib.util.spec_from_file_location("prepare_send_video", _prepare_path)
    if _prepare_spec is None or _prepare_spec.loader is None:
        raise
    _prepare = importlib.util.module_from_spec(_prepare_spec)
    sys.modules[_prepare_spec.name] = _prepare
    _prepare_spec.loader.exec_module(_prepare)
    VideoError = _prepare.VideoError
    VideoInfo = _prepare.VideoInfo
    make_sheet = _prepare.make_sheet
    probe_video = _prepare.probe_video


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".webm"}
OUTCOMES = ("send", "fall", "uncertain")
DEFAULT_OUTPUT = Path("tmp/video-outcome-review.html")
DEFAULT_MANIFEST = Path("data/instagram-manifest.jsonl")


def _normalized_path(path: Path | str, base: Path | None = None) -> str:
    """Return a stable path key for Windows manifests and review files."""
    value = Path(path).expanduser()
    if not value.is_absolute() and base is not None:
        value = base / value
    return os.path.normcase(str(value.resolve()))


def _source_identity(video: Path) -> str:
    """Bind saved decisions to a source path and its current file fingerprint."""
    stat = video.stat()
    value = f"{_normalized_path(video)}\0{stat.st_size}\0{stat.st_mtime_ns}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _label_confirmed(record: dict[str, Any] | None) -> bool:
    if not record or record.get("status") in {"check_climb", "unmatched"}:
        return False
    return record.get("label_confirmed") is True or record.get("match_method") == "description"


def _safe_stem(path: Path) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-")
    return value or "video"


def _href(from_path: Path, target: Path) -> str:
    relative = os.path.relpath(target.resolve(), from_path.parent.resolve()).replace(os.sep, "/")
    return quote(relative, safe="/:@-._~")


def _read_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for record in _read_manifest_records(path):
        for field in ("source_video_path", "video_path"):
            if record.get(field):
                if not isinstance(record[field], str):
                    raise ValueError(f"{path}: {field} must be text")
                key = _normalized_path(record[field], path.resolve().parent)
                if key in records and records[key] is not record:
                    raise ValueError(f"{path}: duplicate video path {record[field]!r}")
                records[key] = record
    return records


def _video_files(directory: Path) -> list[Path]:
    return sorted(
        (path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES),
        key=lambda path: str(path).casefold(),
    )


def _logbook_hint(record: dict[str, Any] | None) -> str:
    if not record:
        return "No manifest match"
    story = str(record.get("send") or "").strip()
    if story.lower().startswith("project"):
        return f"Logbook hint: try ({story})"
    if story:
        return f"Logbook hint: send ({story})"
    return "Logbook hint: matched, outcome not described"


def _json_for_script(value: Any) -> str:
    # Prevent a filename or note from closing the script element early.
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _review_record(
    index: int,
    video: Path,
    sheet: Path | None,
    manifest_record: dict[str, Any] | None,
    output: Path,
) -> dict[str, Any]:
    info = probe_video(video)
    duration = round(info.duration, 3)
    identity = _source_identity(video)
    return {
        "id": f"{identity[:16]}-{_safe_stem(video)}",
        "index": index,
        "source_identity": identity,
        "video_path": str(video.resolve()),
        "video_href": _href(output, video),
        "contact_sheet_href": _href(output, sheet) if sheet else None,
        "duration_seconds": duration,
        "fps": round(info.fps, 3),
        "width": info.width,
        "height": info.height,
        "manifest_status": (manifest_record or {}).get("status"),
        "climb_name": (manifest_record or {}).get("climb_name"),
        "caption": (manifest_record or {}).get("caption"),
        "logbook_hint": _logbook_hint(manifest_record),
        "label_confirmed": _label_confirmed(manifest_record),
        "outcome": "uncertain",
        "start_seconds": 0.0,
        "end_seconds": duration,
        "notes": "",
        "reviewed_at": None,
    }


def _page(records: list[dict[str, Any]], generated_at: str) -> str:
    records_json = _json_for_script(records)
    card_markup: list[str] = []
    for record in records:
        title = record.get("climb_name") or Path(str(record["video_path"])).name
        video_name = Path(str(record["video_path"])).name
        status = record.get("manifest_status") or "not in manifest"
        caption = record.get("caption") or ""
        duration = float(record["duration_seconds"])
        sheet_markup = (
            f'<img loading="lazy" src="{html.escape(str(record["contact_sheet_href"]))}" alt="Timestamped frames from {html.escape(video_name)}">'
            if record.get("contact_sheet_href")
            else '<div class="sheet-placeholder">No sheet generated for this overview.<br>Use the video timeline; run <code>sheet</code> on selected clips for frame timestamps.</div>'
        )
        card_markup.append(
            f"""
            <article class="clip-card" data-id="{html.escape(record['id'])}" data-outcome="uncertain">
              <div class="card-head">
                <div>
                  <p class="eyebrow">Clip {record['index']}</p>
                  <h2>{html.escape(str(title))}</h2>
                  <p class="filename">{html.escape(video_name)}</p>
                </div>
                <span class="status-chip">{html.escape(str(status))}</span>
              </div>
              <div class="media-grid">
                <div>
                  <video controls preload="metadata" src="{html.escape(record['video_href'])}"></video>
                  <p class="media-label">Watch the full clip</p>
                </div>
                <div>
                  {sheet_markup}
                  <p class="media-label">Use timestamps to choose the seated start</p>
                </div>
              </div>
              <div class="evidence">
                <div><span class="label">Duration</span><strong>{duration:.2f}s</strong></div>
                <div><span class="label">Context only</span><strong>{html.escape(record['logbook_hint'])}</strong></div>
              </div>
              <div class="decision" role="group" aria-label="Outcome for {html.escape(video_name)}">
                <span class="label">What actually happened?</span>
                <div class="decision-buttons">
                  <button type="button" data-action="outcome" data-value="send">Send</button>
                  <button type="button" data-action="outcome" data-value="fall">Fall / try</button>
                  <button type="button" data-action="outcome" data-value="uncertain">Uncertain</button>
                </div>
              </div>
              <div class="timing">
                <label>Start seconds <input data-field="start_seconds" type="number" min="0" max="{duration}" step="0.1" value="0"></label>
                <label>End seconds <input data-field="end_seconds" type="number" min="0" max="{duration}" step="0.1" value="{duration}"></label>
                <label class="notes">Notes <input data-field="notes" type="text" placeholder="e.g. touched finish, slipped before top"></label>
              </div>
              <label class="label-check"><input data-field="label_confirmed" type="checkbox"> I have checked the climb name, grade, and angle</label>
              <details class="caption-details">
                <summary>Show posting context</summary>
                <p>{html.escape(caption) if caption else "No caption currently attached"}</p>
              </details>
            </article>
            """
        )
    cards = "\n".join(card_markup)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Video outcome review</title>
  <style>
    :root {{
      --ink: #17212b; --muted: #607080; --paper: #f6f3ee; --card: #fffdf9;
      --line: #ddd7ce; --blue: #1769aa; --green: #147d55; --red: #b34343;
      --yellow: #b47b00; --shadow: 0 10px 28px rgba(23,33,43,.09);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--ink); background: var(--paper); font: 16px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 42px 0 72px; }}
    header {{ display: grid; gap: 16px; margin-bottom: 24px; }}
    h1, h2, p {{ margin-top: 0; }} h1 {{ max-width: 760px; margin-bottom: 0; font: 700 clamp(2rem, 5vw, 4.6rem)/.98 Georgia, serif; letter-spacing: -.045em; }}
    h2 {{ margin-bottom: 2px; font: 700 1.35rem/1.1 Georgia, serif; }}
    .dek {{ max-width: 760px; color: var(--muted); font-size: 1.08rem; }}
    .notice {{ padding: 16px 18px; border: 1px solid #c9d8e5; border-left: 5px solid var(--blue); border-radius: 10px; background: #eef6fc; }}
    .notice strong {{ color: var(--blue); }}
    .toolbar {{ position: sticky; top: 12px; z-index: 5; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 12px; margin: 24px 0; border: 1px solid var(--line); border-radius: 12px; background: rgba(255,253,249,.95); box-shadow: var(--shadow); backdrop-filter: blur(8px); }}
    .counts {{ display: flex; gap: 8px; margin-right: auto; color: var(--muted); font-size: .9rem; }}
    .count {{ padding: 6px 9px; border-radius: 99px; background: #ece8e1; }}
    button, .toolbar label {{ font: inherit; }}
    button {{ cursor: pointer; border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; color: var(--ink); background: white; }}
    button:hover {{ border-color: var(--blue); }}
    button.primary {{ color: white; border-color: var(--blue); background: var(--blue); }}
    .filter.active {{ color: white; border-color: var(--ink); background: var(--ink); }}
    .clip-card {{ display: grid; gap: 16px; margin: 18px 0; padding: 22px; border: 1px solid var(--line); border-radius: 14px; background: var(--card); box-shadow: var(--shadow); }}
    .clip-card[data-outcome="send"] {{ border-color: #9fd8bd; }} .clip-card[data-outcome="fall"] {{ border-color: #e8b0b0; }}
    .card-head {{ display: flex; justify-content: space-between; gap: 16px; align-items: start; }}
    .eyebrow, .filename, .media-label, .label {{ color: var(--muted); font-size: .78rem; letter-spacing: .05em; text-transform: uppercase; }}
    .eyebrow {{ margin-bottom: 5px; }} .filename {{ margin-bottom: 0; overflow-wrap: anywhere; }}
    .status-chip {{ flex: none; padding: 5px 9px; border-radius: 99px; color: var(--muted); background: #ece8e1; font-size: .78rem; }}
    .media-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    video, img {{ display: block; width: 100%; max-height: 520px; object-fit: contain; border: 1px solid var(--line); border-radius: 8px; background: #e6e1d9; }}
    .sheet-placeholder {{ display: grid; place-items: center; min-height: 190px; padding: 24px; border: 1px dashed var(--line); border-radius: 8px; color: var(--muted); background: #f0ede7; text-align: center; }}
    .media-label {{ margin: 7px 0 0; }}
    .evidence {{ display: flex; flex-wrap: wrap; gap: 16px; padding: 12px; border-radius: 8px; background: #f0ede7; }}
    .evidence > div {{ display: grid; gap: 2px; }} .evidence strong {{ font-size: .94rem; }}
    .decision {{ display: grid; gap: 8px; }} .decision-buttons {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .decision button[data-selected="true"] {{ color: white; border-color: var(--ink); background: var(--ink); }}
    .decision button[data-value="send"][data-selected="true"] {{ border-color: var(--green); background: var(--green); }}
    .decision button[data-value="fall"][data-selected="true"] {{ border-color: var(--red); background: var(--red); }}
    .timing {{ display: flex; flex-wrap: wrap; gap: 12px; }} .timing label {{ display: grid; gap: 5px; color: var(--muted); font-size: .82rem; }}
    input {{ min-width: 100px; padding: 8px 9px; border: 1px solid var(--line); border-radius: 7px; font: inherit; color: var(--ink); background: white; }}
    .label-check {{ display: flex; align-items: center; gap: 8px; }} .label-check input {{ min-width: 0; }}
    .notes {{ flex: 1 1 280px; }} .notes input {{ width: 100%; }}
    .caption-details {{ color: var(--muted); font-size: .9rem; }} .caption-details p {{ white-space: pre-wrap; margin: 8px 0 0; }}
    .hidden {{ display: none; }} footer {{ margin-top: 30px; color: var(--muted); font-size: .85rem; }}
    @media (max-width: 760px) {{ main {{ width: min(100% - 20px, 620px); padding-top: 24px; }} .media-grid {{ grid-template-columns: 1fr; }} .toolbar {{ position: static; }} .counts {{ flex-basis: 100%; }} .card-head {{ display: grid; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <p class="eyebrow">BoardLib · local review</p>
    <h1>Did the move finish, or did gravity win?</h1>
    <p class="dek">Review each clip before trimming or posting. Choose the actual outcome, mark the seated start, and download one review file when you’re done.</p>
    <div class="notice"><strong>Safety boundary:</strong> the logbook hint is only context. This page does not silently decide that a video is a send. “Uncertain” is a valid answer.</div>
  </header>
  <section class="toolbar" aria-label="Review controls">
    <div class="counts"><span class="count" id="count-all">0 clips</span><span class="count" id="count-send">0 sends</span><span class="count" id="count-fall">0 falls/tries</span><span class="count" id="count-uncertain">0 uncertain</span></div>
    <button class="filter active" data-filter="all">All</button><button class="filter" data-filter="uncertain">Needs review</button><button class="filter" data-filter="send">Sends</button><button class="filter" data-filter="fall">Falls / tries</button>
    <button id="download" class="primary">Download review JSON</button><label class="import-button">Load JSON <input id="import" type="file" accept="application/json" hidden></label>
  </section>
  <section id="cards">{cards}</section>
  <footer>Generated {html.escape(generated_at)}. Files stay local; the downloaded review JSON is the handoff to the manifest updater.</footer>
</main>
<script>
const RECORDS = {records_json};
const STORAGE_KEY = "boardlib-video-outcome-review-v2";
const OUTCOMES = ["send", "fall", "uncertain"];
const DECISION_FIELDS = ["outcome", "start_seconds", "end_seconds", "notes", "reviewed_at", "label_confirmed"];
const state = {{}};
for (const record of RECORDS) state[record.id] = {{ ...record }};
function mergeDecision(saved) {{
  const record = saved && state[saved.id];
  if (!record || saved.video_path !== record.video_path || saved.source_identity !== record.source_identity) return;
  if (!OUTCOMES.includes(saved.outcome)) return;
  for (const field of ["start_seconds", "end_seconds"]) if (!Number.isFinite(saved[field]) || saved[field] < 0 || saved[field] > record.duration_seconds) return;
  if (saved.start_seconds >= saved.end_seconds || typeof saved.notes !== "string" || typeof saved.label_confirmed !== "boolean") return;
  if (saved.reviewed_at !== null && (typeof saved.reviewed_at !== "string" || !/(Z|[+-]\\d{{2}}:\\d{{2}})$/.test(saved.reviewed_at) || !Number.isFinite(Date.parse(saved.reviewed_at)))) return;
  if (saved.outcome !== "uncertain" && !saved.reviewed_at) return;
  for (const field of DECISION_FIELDS) record[field] = saved[field];
}}
try {{
  const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}");
  for (const record of Object.values(saved)) mergeDecision(record);
}} catch (error) {{ console.warn("Could not load local review state", error); }}
function save() {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }}
function render() {{
  const counts = {{ send: 0, fall: 0, uncertain: 0 }};
  for (const record of Object.values(state)) counts[OUTCOMES.includes(record.outcome) ? record.outcome : "uncertain"] += 1;
  document.getElementById("count-all").textContent = `${{RECORDS.length}} clips`;
  document.getElementById("count-send").textContent = `${{counts.send}} sends`;
  document.getElementById("count-fall").textContent = `${{counts.fall}} falls/tries`;
  document.getElementById("count-uncertain").textContent = `${{counts.uncertain}} uncertain`;
  for (const card of document.querySelectorAll(".clip-card")) {{
    const record = state[card.dataset.id];
    card.dataset.outcome = record.outcome;
    for (const button of card.querySelectorAll("[data-action=outcome]")) button.dataset.selected = String(button.dataset.value === record.outcome);
    for (const field of card.querySelectorAll("[data-field]")) {{
      if (field.type === "checkbox") field.checked = record[field.dataset.field] === true;
      else field.value = record[field.dataset.field] ?? "";
    }}
  }}
}}
function setOutcome(id, outcome) {{ state[id].outcome = outcome; state[id].reviewed_at = new Date().toISOString(); save(); render(); }}
document.querySelectorAll("[data-action=outcome]").forEach(button => button.addEventListener("click", () => setOutcome(button.closest(".clip-card").dataset.id, button.dataset.value)));
document.querySelectorAll("[data-field]").forEach(field => field.addEventListener("input", () => {{
  const record = state[field.closest(".clip-card").dataset.id];
  record[field.dataset.field] = field.type === "checkbox" ? field.checked : field.type === "number" ? (field.value === "" ? null : Number(field.value)) : field.value;
  if (record.outcome !== "uncertain") record.reviewed_at = new Date().toISOString();
  save();
}}));
document.querySelectorAll(".filter").forEach(button => button.addEventListener("click", () => {{
  document.querySelectorAll(".filter").forEach(item => item.classList.toggle("active", item === button));
  const filter = button.dataset.filter;
  document.querySelectorAll(".clip-card").forEach(card => card.classList.toggle("hidden", filter !== "all" && state[card.dataset.id].outcome !== filter));
}}));
document.getElementById("download").addEventListener("click", () => {{
  const payload = {{ generated_at: new Date().toISOString(), reviews: Object.values(state) }};
  const blob = new Blob([JSON.stringify(payload, null, 2)], {{ type: "application/json" }});
  const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = "video-outcome-review.json"; link.click(); URL.revokeObjectURL(link.href);
}});
document.getElementById("import").addEventListener("change", event => {{
  const file = event.target.files[0]; if (!file) return;
  const reader = new FileReader(); reader.onload = () => {{
    try {{
      const loaded = JSON.parse(reader.result); const reviews = Array.isArray(loaded) ? loaded : loaded.reviews;
      if (!Array.isArray(reviews)) throw new Error("Expected a list of reviews");
      for (const record of reviews) mergeDecision(record);
      save(); render();
    }} catch (error) {{ alert("Could not load the review file: " + error.message); }}
  }}; reader.readAsText(file);
}});
render();
</script>
</body>
</html>
"""


def create_review_page(
    directory: Path,
    output: Path = DEFAULT_OUTPUT,
    *,
    interval: float = 0.5,
    columns: int = 4,
    manifest: Path = DEFAULT_MANIFEST,
    sheets: bool = True,
) -> list[dict[str, Any]]:
    """Generate contact sheets and the local review HTML for a video folder."""
    if not directory.is_dir():
        raise VideoError(f"video directory not found: {directory}")
    if not math.isfinite(interval) or interval <= 0 or columns <= 0:
        raise VideoError("--interval and --columns must be greater than zero")
    videos = _video_files(directory)
    if not videos:
        raise VideoError(f"no video files found under {directory}")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    asset_dir = output.with_suffix("")
    asset_dir.mkdir(parents=True, exist_ok=True)
    manifest_records = _read_manifest(manifest)
    records = []
    for index, video in enumerate(videos, 1):
        sheet = asset_dir / f"{index:03d}-{_safe_stem(video)}.jpg"
        if sheets:
            make_sheet(video, sheet, interval, columns)
        records.append(_review_record(index, video, sheet if sheets else None, manifest_records.get(_normalized_path(video)), output))
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    output.write_text(_page(records, generated_at), encoding="utf-8")
    review_json = output.with_suffix(".json")
    review_json.write_text(json.dumps({"generated_at": generated_at, "reviews": records}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    print(f"Wrote {review_json}")
    print(f"Prepared {len(records)} videos; all outcomes start as uncertain until reviewed.")
    if not sheets:
        print("Contact sheets were skipped; use the video timeline or run the sheet command for selected clips.")
    return records


def _read_reviews(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    reviews = data.get("reviews") if isinstance(data, dict) else data
    if not isinstance(reviews, list):
        raise ValueError(f"{path}: expected a JSON list or an object with a reviews list")
    if any(not isinstance(review, dict) for review in reviews):
        raise ValueError(f"{path}: every review must be an object")
    return reviews


def _review_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("reviewed_at must be an ISO timestamp or null")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("reviewed_at must be a valid ISO timestamp") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("reviewed_at must include a timezone")
    return value


def _review_number(review: dict[str, Any], field: str) -> float | None:
    value = review.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite, non-negative number")
    return float(value)


def _validate_reviews(reviews: list[dict[str, Any]], base: Path) -> dict[str, dict[str, Any]]:
    """Validate the whole handoff before touching any manifest record."""
    by_path: dict[str, dict[str, Any]] = {}
    ids = set()
    for review in reviews:
        path = review.get("video_path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("every review needs a non-empty video_path")
        key = _normalized_path(path, base)
        if key in by_path:
            raise ValueError(f"duplicate review for {path!r}")
        if "id" in review:
            if not isinstance(review["id"], str) or not review["id"] or review["id"] in ids:
                raise ValueError("review IDs must be non-empty and unique")
            ids.add(review["id"])
        outcome = review.get("outcome")
        if outcome not in OUTCOMES:
            raise ValueError(f"review contains unsupported outcome: {outcome!r}")
        reviewed_at = _review_timestamp(review.get("reviewed_at"))
        if outcome != "uncertain" and reviewed_at is None:
            raise ValueError("send/fall decisions require reviewed_at from an actual review")
        start = _review_number(review, "start_seconds")
        end = _review_number(review, "end_seconds")
        duration = _review_number(review, "duration_seconds")
        if start is not None and end is not None and start >= end:
            raise ValueError("start_seconds must be before end_seconds")
        if duration is not None:
            if duration <= 0 or (start is not None and start >= duration) or (end is not None and end > duration):
                raise ValueError("review timestamps must fall within duration_seconds")
        if "label_confirmed" in review and type(review["label_confirmed"]) is not bool:
            raise ValueError("label_confirmed must be a boolean")
        if "notes" in review and not isinstance(review["notes"], str):
            raise ValueError("notes must be text")
        identity = review.get("source_identity")
        if identity is not None:
            if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
                raise ValueError("source_identity must be a source-file fingerprint")
            source = Path(key)
            if not source.is_file() or _source_identity(source) != identity:
                raise ValueError(f"source video changed since review: {path!r}")
        by_path[key] = review
    return by_path


def _write_manifest(path: Path, records: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def apply_reviews(
    review_path: Path,
    *,
    manifest: Path | None = None,
    approve_sends: bool = False,
    skip_falls: bool = False,
) -> int:
    """Apply human review fields; posting-state changes require explicit flags."""
    reviews = _read_reviews(review_path)
    by_path = _validate_reviews(reviews, review_path.resolve().parent)
    if manifest is None:
        print(f"Validated {len(reviews)} reviews; no manifest was changed.")
        return len(reviews)

    records = _read_manifest_records(manifest)
    seen_sources = set()
    for record in records:
        source = record.get("source_video_path") or record.get("video_path")
        if source:
            if not isinstance(source, str):
                raise ValueError("manifest video paths must be text")
            key = _normalized_path(source, manifest.resolve().parent)
            if key in seen_sources:
                raise ValueError(f"manifest has duplicate source video path {source!r}")
            seen_sources.add(key)
    updated = 0
    for record in records:
        source = record.get("source_video_path") or record.get("video_path")
        review = by_path.get(_normalized_path(source, manifest.resolve().parent)) if source else None
        if not review:
            continue
        outcome = review["outcome"]
        source_duration = record.get("source_duration_seconds")
        if source_duration is not None:
            duration = _review_number({"duration_seconds": source_duration}, "duration_seconds")
            start, end = review.get("start_seconds"), review.get("end_seconds")
            if duration is None or duration <= 0 or (start is not None and start >= duration) or (end is not None and end > duration):
                raise ValueError("review timestamps must fall within the manifest source duration")
        published = record.get("status") == "published" or bool(record.get("published_media_id"))
        label_confirmed = review.get("label_confirmed", _label_confirmed(record))
        if approve_sends and outcome == "send" and not published:
            if review.get("start_seconds") is None:
                raise ValueError("approving a send requires reviewed start_seconds")
            if label_confirmed is not True:
                raise ValueError("confirm the climb label before approving a send")
        record["video_outcome"] = outcome
        record["video_reviewed_at"] = review.get("reviewed_at")
        for field in ("start_seconds", "end_seconds"):
            if field in review:
                record[field] = review[field]
        record["video_review_notes"] = review.get("notes") or ""
        record["label_confirmed"] = label_confirmed is True
        if review.get("label_confirmed") is True:
            record["needs_label_confirmation"] = False
        if review.get("source_identity"):
            record["video_review_source_identity"] = review["source_identity"]
        if approve_sends and outcome == "send" and not published:
            record["status"] = "approved"
        if skip_falls and outcome == "fall" and not published:
            record["status"] = "skip"
        updated += 1
    _write_manifest(manifest, records)
    print(f"Updated {updated} manifest records in {manifest}")
    if not (approve_sends or skip_falls):
        print("Posting statuses were left unchanged; use --approve-sends or --skip-falls explicitly if desired.")
    return updated


def _read_manifest_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"manifest not found: {path}")
    records = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON ({error.msg})") from error
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            records.append(record)
    return records


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subcommands = root.add_subparsers(dest="command", required=True)
    review = subcommands.add_parser("review", help="create contact sheets and a local outcome review page")
    review.add_argument("directory", type=Path)
    review.add_argument("--output", "-o", type=Path, default=DEFAULT_OUTPUT)
    review.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    review.add_argument("--interval", type=float, default=0.5, help="seconds between sheet frames (default: 0.5)")
    review.add_argument("--columns", type=int, default=4)
    review.add_argument("--no-sheets", action="store_true", help="skip FFmpeg contact-sheet generation for a fast overview")

    apply = subcommands.add_parser("apply", help="apply a downloaded human review JSON to a manifest")
    apply.add_argument("review_json", type=Path)
    apply.add_argument("--manifest", type=Path)
    apply.add_argument("--approve-sends", action="store_true", help="set reviewed sends to status=approved")
    apply.add_argument("--skip-falls", action="store_true", help="set reviewed falls/tries to status=skip")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "review":
            create_review_page(args.directory, args.output, interval=args.interval, columns=args.columns, manifest=args.manifest, sheets=not args.no_sheets)
        else:
            apply_reviews(args.review_json, manifest=args.manifest, approve_sends=args.approve_sends, skip_falls=args.skip_falls)
        return 0
    except (OSError, ValueError, VideoError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
