"""Generate a standalone HTML visualizer for a BoardLib logbook CSV."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


FIELDS = [
    "board",
    "angle",
    "climb_name",
    "date",
    "logged_grade",
    "displayed_grade",
    "is_benchmark",
    "tries",
    "is_mirror",
    "sessions_count",
    "tries_total",
    "is_repeat",
    "is_ascent",
    "comment",
]


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def parse_int(value: str, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def clean_text(value: str) -> str:
    value = str(value or "").strip()
    return "" if value.lower() == "nan" else value


def parse_date(value: str) -> dt.datetime:
    value = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            pass
    return dt.datetime.fromisoformat(value)


def grade_key(grade: str) -> tuple[int, str]:
    if not grade:
        return (10_000, "")
    match = re.search(r"V(\d+)", grade)
    if match:
        return (int(match.group(1)), grade)
    return (9_000, grade)


def load_rows(csv_path: Path) -> list[dict[str, object]]:
    rows = []
    with csv_path.open(newline="", encoding="utf-8-sig") as input_file:
        for row in csv.DictReader(input_file):
            if not any(row.values()):
                continue
            climbed_at = parse_date(row["date"])
            logged_grade = (row.get("logged_grade") or "").strip()
            displayed_grade = (row.get("displayed_grade") or "").strip()
            rows.append(
                {
                    "board": row.get("board", ""),
                    "angle": parse_int(row.get("angle", "")),
                    "climb_name": clean_text(row.get("climb_name", "")),
                    "date": climbed_at.isoformat(timespec="seconds"),
                    "session_date": climbed_at.date().isoformat(),
                    "logged_grade": logged_grade or displayed_grade or "Ungraded",
                    "displayed_grade": displayed_grade,
                    "is_benchmark": parse_bool(row.get("is_benchmark", "")),
                    "tries": parse_int(row.get("tries", ""), 1),
                    "is_mirror": parse_bool(row.get("is_mirror", "")),
                    "sessions_count": parse_int(row.get("sessions_count", "")),
                    "tries_total": parse_int(row.get("tries_total", "")),
                    "is_repeat": parse_bool(row.get("is_repeat", "")),
                    "is_ascent": parse_bool(row.get("is_ascent", "")),
                    "comment": clean_text(row.get("comment", "")),
                }
            )
    return rows


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    by_date: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_date[str(row["session_date"])].append(row)

    sessions = []
    for session_date, session_rows in sorted(by_date.items()):
        ascents = [row for row in session_rows if row["is_ascent"]]
        grade_counts = Counter(str(row["logged_grade"]) for row in ascents)
        sessions.append(
            {
                "date": session_date,
                "board": session_rows[0]["board"] if session_rows else "",
                "total_entries": len(session_rows),
                "ascents": len(ascents),
                "attempt_rows": len(session_rows) - len(ascents),
                "unique_climbs": len({row["climb_name"] for row in ascents}),
                "total_tries": sum(int(row["tries"]) for row in session_rows),
                "benchmarks": sum(1 for row in ascents if row["is_benchmark"]),
                "repeats": sum(1 for row in ascents if row["is_repeat"]),
                "angles": sorted({row["angle"] for row in session_rows}),
                "grade_counts": dict(sorted(grade_counts.items(), key=lambda item: grade_key(item[0]))),
            }
        )

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "row_count": len(rows),
        "sessions": sessions,
        "grade_order": sorted(
            {str(row["logged_grade"]) for row in rows if row["is_ascent"]},
            key=grade_key,
        ),
    }


def render_html(csv_path: Path, rows: list[dict[str, object]], summary: dict[str, object]) -> str:
    payload = json.dumps({"rows": rows, "summary": summary}, separators=(",", ":"))
    title = f"BoardLib Logbook Visualizer - {html.escape(csv_path.name)}"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17211f;
      --muted: #64706b;
      --line: #d8dfdb;
      --paper: #f8faf8;
      --panel: #ffffff;
      --accent: #0f766e;
      --accent-2: #b45309;
      --accent-3: #365314;
      --soft: #edf6f4;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--paper);
    }}
    button, select {{
      font: inherit;
    }}
    .shell {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 20px 44px;
    }}
    header {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 20px;
      align-items: end;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      font-size: clamp(28px, 5vw, 48px);
      line-height: 1;
      letter-spacing: 0;
    }}
    .subhead {{
      margin: 10px 0 0;
      color: var(--muted);
      max-width: 720px;
    }}
    .controls {{
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
    }}
    .icon-button, .copy-button {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      min-width: 40px;
      height: 40px;
      border-radius: 8px;
      cursor: pointer;
    }}
    .copy-button {{
      padding: 0 14px;
      min-width: auto;
    }}
    select {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      height: 40px;
      padding: 0 12px;
      color: var(--ink);
    }}
    main {{
      display: grid;
      gap: 22px;
      margin-top: 22px;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(5, minmax(130px, 1fr));
      gap: 10px;
    }}
    .stat {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 92px;
    }}
    .stat strong {{
      display: block;
      font-size: 30px;
      line-height: 1;
      letter-spacing: 0;
    }}
    .stat span {{
      display: block;
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .band {{
      background: var(--panel);
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      padding: 18px 0;
    }}
    .section-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(300px, .9fr);
      gap: 22px;
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 18px;
      letter-spacing: 0;
    }}
    .chart {{
      display: grid;
      gap: 10px;
    }}
    .bar-row {{
      display: grid;
      grid-template-columns: 82px minmax(140px, 1fr) 44px;
      gap: 10px;
      align-items: center;
      min-height: 32px;
    }}
    .bar-track {{
      height: 18px;
      background: #e7ece9;
      border-radius: 4px;
      overflow: hidden;
    }}
    .bar {{
      height: 100%;
      min-width: 4px;
      background: linear-gradient(90deg, var(--accent), var(--accent-2));
    }}
    .bar-label, .bar-count {{
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }}
    .log-panel {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: var(--panel);
    }}
    .log-toolbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px;
      background: var(--soft);
      border-bottom: 1px solid var(--line);
    }}
    pre {{
      margin: 0;
      padding: 14px;
      white-space: pre-wrap;
      line-height: 1.45;
      font-size: 13px;
      max-height: 280px;
      overflow: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 10px 8px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      background: #f1f5f3;
      position: sticky;
      top: 0;
    }}
    .table-wrap {{
      max-height: 440px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }}
    .pill-list {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 4px 8px;
      background: #f4efe7;
      color: #5f3d13;
      font-size: 12px;
    }}
    @media (max-width: 840px) {{
      header, .section-grid {{
        grid-template-columns: 1fr;
      }}
      .controls {{
        justify-content: flex-start;
      }}
      .stats {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
      .bar-row {{
        grid-template-columns: 68px minmax(80px, 1fr) 36px;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>Logbook Visualizer</h1>
        <p class="subhead">Session-by-session grade distribution and workout log text generated from {html.escape(csv_path.name)}.</p>
      </div>
      <div class="controls">
        <button class="icon-button" id="prevDay" title="Previous session" aria-label="Previous session">&lt;</button>
        <select id="sessionSelect" aria-label="Session date"></select>
        <button class="icon-button" id="nextDay" title="Next session" aria-label="Next session">&gt;</button>
      </div>
    </header>

    <main>
      <section class="stats" id="stats"></section>
      <section class="band">
        <div class="section-grid">
          <div>
            <h2>Logged Grade Counts</h2>
            <div class="chart" id="gradeChart"></div>
          </div>
          <div class="log-panel">
            <div class="log-toolbar">
              <h2>Workout Log</h2>
              <button class="copy-button" id="copyLog">Copy</button>
            </div>
            <pre id="workoutLog"></pre>
          </div>
        </div>
      </section>
      <section>
        <h2>Climbs</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Climb</th>
                <th>Grade</th>
                <th>Angle</th>
                <th>Tries</th>
                <th>Type</th>
              </tr>
            </thead>
            <tbody id="climbRows"></tbody>
          </table>
        </div>
      </section>
      <section>
        <h2>All Sessions</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Ascents</th>
                <th>Tries</th>
                <th>Grades</th>
              </tr>
            </thead>
            <tbody id="sessionRows"></tbody>
          </table>
        </div>
      </section>
    </main>
  </div>

  <script>
    const payload = {payload};
    const rows = payload.rows;
    const sessions = payload.summary.sessions;
    const gradeOrder = payload.summary.grade_order;
    let selectedIndex = Math.max(0, sessions.length - 1);

    const $ = (id) => document.getElementById(id);

    function formatDate(value) {{
      const date = new Date(value + "T00:00:00");
      return date.toLocaleDateString(undefined, {{ weekday: "short", year: "numeric", month: "short", day: "numeric" }});
    }}

    function timeOf(value) {{
      return new Date(value).toLocaleTimeString(undefined, {{ hour: "numeric", minute: "2-digit" }});
    }}

    function sessionRows(session) {{
      return rows
        .filter((row) => row.session_date === session.date)
        .sort((a, b) => a.date.localeCompare(b.date));
    }}

    function gradeSummaryText(gradeCounts) {{
      return gradeOrder
        .filter((grade) => gradeCounts[grade])
        .map((grade) => `${{grade}} x${{gradeCounts[grade]}}`)
        .join(", ") || "No sends logged";
    }}

    function workoutText(session) {{
      const angles = session.angles.length ? session.angles.join("/") + " deg" : "angle unknown";
      const lines = [
        `${{formatDate(session.date)}} - ${{session.board}} @ ${{angles}}`,
        `${{session.ascents}} sends, ${{session.total_tries}} total tries, ${{session.unique_climbs}} unique climbs`,
        `Grades: ${{gradeSummaryText(session.grade_counts)}}`,
        `Benchmarks: ${{session.benchmarks}}; repeats: ${{session.repeats}}`
      ];
      const comments = sessionRows(session)
        .filter((row) => row.comment)
        .map((row) => `- ${{row.climb_name}}: ${{row.comment}}`);
      if (comments.length) {{
        lines.push("Notes:", ...comments);
      }}
      return lines.join("\\n");
    }}

    function renderStats(session) {{
      const stats = [
        ["Ascents", session.ascents],
        ["Unique climbs", session.unique_climbs],
        ["Total tries", session.total_tries],
        ["Benchmarks", session.benchmarks],
        ["Repeats", session.repeats]
      ];
      $("stats").innerHTML = stats.map(([label, value]) => `
        <div class="stat"><strong>${{value}}</strong><span>${{label}}</span></div>
      `).join("");
    }}

    function renderChart(session) {{
      const maxCount = Math.max(1, ...Object.values(session.grade_counts));
      const rowsHtml = gradeOrder
        .filter((grade) => session.grade_counts[grade])
        .map((grade) => {{
          const count = session.grade_counts[grade];
          const width = Math.max(4, Math.round((count / maxCount) * 100));
          return `
            <div class="bar-row">
              <div class="bar-label">${{grade}}</div>
              <div class="bar-track"><div class="bar" style="width: ${{width}}%"></div></div>
              <div class="bar-count">${{count}}</div>
            </div>`;
        }}).join("");
      $("gradeChart").innerHTML = rowsHtml || "<p class='subhead'>No ascents logged for this day.</p>";
    }}

    function renderClimbs(session) {{
      $("climbRows").innerHTML = sessionRows(session).map((row) => `
        <tr>
          <td>${{timeOf(row.date)}}</td>
          <td>${{row.climb_name}}${{row.is_mirror ? " (mirror)" : ""}}</td>
          <td>${{row.logged_grade}}</td>
          <td>${{row.angle}}</td>
          <td>${{row.tries}}</td>
          <td>${{row.is_ascent ? "send" : "attempt"}}${{row.is_benchmark ? ", benchmark" : ""}}</td>
        </tr>
      `).join("");
    }}

    function renderSessionTable() {{
      $("sessionRows").innerHTML = sessions.slice().reverse().map((session) => `
        <tr>
          <td>${{formatDate(session.date)}}</td>
          <td>${{session.ascents}}</td>
          <td>${{session.total_tries}}</td>
          <td><div class="pill-list">${{Object.entries(session.grade_counts).map(([grade, count]) => `<span class="pill">${{grade}} x${{count}}</span>`).join("")}}</div></td>
        </tr>
      `).join("");
    }}

    function render() {{
      const session = sessions[selectedIndex];
      $("sessionSelect").value = String(selectedIndex);
      renderStats(session);
      renderChart(session);
      renderClimbs(session);
      $("workoutLog").textContent = workoutText(session);
    }}

    $("sessionSelect").innerHTML = sessions.map((session, index) => `
      <option value="${{index}}">${{formatDate(session.date)}} (${{session.ascents}} sends)</option>
    `).join("");
    $("sessionSelect").addEventListener("change", (event) => {{
      selectedIndex = Number(event.target.value);
      render();
    }});
    $("prevDay").addEventListener("click", () => {{
      selectedIndex = Math.max(0, selectedIndex - 1);
      render();
    }});
    $("nextDay").addEventListener("click", () => {{
      selectedIndex = Math.min(sessions.length - 1, selectedIndex + 1);
      render();
    }});
    $("copyLog").addEventListener("click", async () => {{
      await navigator.clipboard.writeText($("workoutLog").textContent);
      $("copyLog").textContent = "Copied";
      setTimeout(() => $("copyLog").textContent = "Copy", 1200);
    }});

    renderSessionTable();
    render();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="BoardLib logbook CSV path")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="HTML output path. Defaults to <csv_path stem>-report.html",
    )
    args = parser.parse_args()

    output_path = args.output or args.csv_path.with_name(f"{args.csv_path.stem}-report.html")
    rows = load_rows(args.csv_path)
    summary = summarize(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html(args.csv_path, rows, summary), encoding="utf-8")
    print(f"Wrote {output_path}")
    print(f"Sessions: {len(summary['sessions'])}; rows: {summary['row_count']}")


if __name__ == "__main__":
    main()
