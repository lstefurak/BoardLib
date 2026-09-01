"""Structural and behavioural checks for the Climbs (send history) view."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup


DOCS_INDEX = Path(__file__).resolve().parents[1] / "docs" / "index.html"
DOCS_APP = Path(__file__).resolve().parents[1] / "docs" / "app.js"
NODE = shutil.which("node")

# Column keys in table order; each <th data-sort> must match app.js's
# CLIMB_COLUMNS.
SORT_COLUMNS = [
    "name",
    "grade",
    "angle",
    "sends",
    "tries",
    "sessions",
    "firstSend",
    "recent",
    "communitySends",
    "quality",
]


def _soup():
    return BeautifulSoup(DOCS_INDEX.read_text(encoding="utf-8"), "html.parser")


def test_dashboard_has_view_switcher():
    soup = _soup()

    tablist = soup.find(attrs={"role": "tablist"})
    assert tablist is not None
    tabs = tablist.find_all("button", attrs={"role": "tab"})
    assert [tab.get("id") for tab in tabs] == ["viewSessionsTab", "viewClimbsTab"]
    assert tabs[1].get_text(strip=True) == "All-time climbs"
    # Sessions is the default view; Climbs starts unselected.
    assert tabs[0].get("aria-selected") == "true"
    assert tabs[1].get("aria-selected") == "false"
    assert tabs[0].get("aria-controls") == "sessionsView"
    assert tabs[1].get("aria-controls") == "climbsView"


def test_views_are_panels_and_climbs_starts_hidden():
    soup = _soup()

    sessions_view = soup.find(id="sessionsView")
    climbs_view = soup.find(id="climbsView")
    assert sessions_view is not None
    assert climbs_view is not None
    assert "is-hidden" in climbs_view.get("class", [])
    assert "is-hidden" not in sessions_view.get("class", [])
    # The session-scoped widgets moved inside the sessions panel.
    assert sessions_view.find(id="sessionSelect") is not None
    assert sessions_view.find(id="climbRows") is not None


def test_climbs_view_has_controls_and_history_table():
    soup = _soup()
    climbs_view = soup.find(id="climbsView")

    assert climbs_view.find(id="climbSearch") is not None
    # Sorting moved into the column headers; the select and Reverse button are gone.
    assert climbs_view.find(id="climbSort") is None
    assert climbs_view.find(id="climbSortDirection") is None
    group_toggle = climbs_view.find(id="climbGroupVariants")
    assert group_toggle is not None
    assert group_toggle.has_attr("checked")
    filters = climbs_view.find(id="climbFilters")
    assert filters is not None
    assert {chip.get("data-filter") for chip in filters.find_all("button")} == {
        "all",
        "sent",
        "project",
        "repeated",
        "flashed",
        "benchmark",
    }
    # Angle is a multi-select filter, filled in from the loaded data by app.js.
    angles = climbs_view.find(id="climbAngles")
    assert angles is not None
    assert angles.get("role") == "group"
    assert soup.find(id=angles.get("aria-labelledby")).get_text(strip=True) == "Angle"
    assert angles.find_all("button") == []

    table = climbs_view.find(id="climbHistoryTable")
    assert table is not None
    assert climbs_view.find(id="climbHistoryRows") is not None
    headers = table.find("thead").find_all("th")
    assert [th.get("data-sort") for th in headers] == SORT_COLUMNS
    assert [th.get_text(" ", strip=True) for th in headers] == [
        "Climb",
        "Grade",
        "Angle",
        "Sends",
        "Tries",
        "Sessions",
        "First send",
        "Last climbed",
        "Community Sends",
        "Community Stars",
    ]


def test_climb_table_headers_are_sort_buttons():
    soup = _soup()
    headers = soup.find(id="climbHistoryTable").find("thead").find_all("th")

    for th in headers:
        assert th.get("scope") == "col"
        button = th.find("button", class_="sort-button")
        assert button is not None
        assert button.get("type") == "button"
        assert button.find(class_="sort-arrow").get("aria-hidden") == "true"
        # The active column's aria-sort is applied by app.js, never hardcoded.
        assert not th.has_attr("aria-sort")

    # Community sends/stars are a labelled group so neither reads as the
    # climber's own Sends column; the group can be hidden as one unit.
    community = [th for th in headers if "is-community" in th.get("class", [])]
    assert [th.get("data-sort") for th in community] == ["communitySends", "quality"]
    assert "is-community-start" in community[0].get("class", [])
    assert [th.find(class_="th-group").get_text(strip=True) for th in community] == [
        "Community",
        "Community",
    ]


def test_climbs_view_has_a_legend_slot_for_angle_chips():
    soup = _soup()
    legend = soup.find(id="angleLegend")

    assert legend is not None
    # Chips in the table carry their own accessible names; the legend is a
    # visual decoder only, filled in by app.js from the same glyph helper.
    assert legend.get("aria-hidden") == "true"
    assert legend.get_text(strip=True) == ""


def test_climb_rendering_escapes_board_authored_content():
    # Climb names, grades, and comments are authored by other board users, so
    # every interpolation in the climbs renderers must go through escapeHtml.
    js = DOCS_APP.read_text(encoding="utf-8")

    assert "escapeHtml(climb.name)" in js
    assert "escapeHtml(climb.grade)" in js
    assert "escapeHtml(entry.comment)" in js
    assert "escapeHtml(entry.logged_grade)" in js


def test_climbs_group_by_stable_climb_identity():
    js = DOCS_APP.read_text(encoding="utf-8")

    assert "climb_uuid: cleanText(row.climb_uuid)" in js
    assert "return row.climb_uuid || row.climb_name.toLowerCase()" in js
    assert "climbVariantKey(row)" in js


def test_climb_chips_and_headers_are_wired_in_app_js():
    js = DOCS_APP.read_text(encoding="utf-8")

    # One sort key per header, plus the header click handler and aria-sort sync.
    for column in SORT_COLUMNS:
        assert f"  {column}: {{ key: (climb) =>" in js
    assert 'th[data-sort]' in js
    assert 'setAttribute("aria-sort"' in js
    assert 'removeAttribute("aria-sort")' in js
    # Every angle chip has an accessible name built from escaped text.
    assert 'role="img" aria-label="${escapeHtml(label)}"' in js
    assert "climb.angles.some((angle) => state.climbAngles.has(angle))" in js
    # The legend is rendered from the same glyph helper as the table chips.
    assert '$("angleLegend").innerHTML = angleLegendHtml();' in js
    # Community quality renders as three fractional stars; both community
    # columns hide together.
    assert "starsHtml(climb.quality)" in js
    assert 'classList.toggle("no-community", !hasCommunity)' in js


# Runs docs/app.js in Node with a stub DOM so the sorting/filtering helpers can
# be exercised with real data. Skipped when Node is not installed.
APP_JS_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf8");

// app.js wires up the page at load time; a permissive stub stands in for the
// DOM so the pure data helpers can be exercised.
function stub() {
  return new Proxy(function () {}, {
    get(target, prop) {
      if (typeof prop === "symbol") return undefined;
      if (prop === "length") return 0;
      if (prop === "files") return [];
      return stub();
    },
    apply() {
      return stub();
    },
  });
}

const sandbox = {
  document: stub(),
  location: { hostname: "localhost" },
  sessionStorage: { getItem: () => "", setItem() {}, removeItem() {} },
  navigator: {},
  console,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const probe = `
(() => {
  const rows = normalizeRows([
    // Alpha: sent at 40 (original), project at 45 (mirror); community stats present.
    { climb_uuid: "a", climb_name: "Alpha", date: "2024-01-01 10:00:00", angle: "40", logged_grade: "6c/V5", tries: "2", is_mirror: "False", is_ascent: "True", ascensionist_count: "120", quality_average: "2.5" },
    { climb_uuid: "a", climb_name: "Alpha", date: "2024-02-01 10:00:00", angle: "45", logged_grade: "6c/V5", tries: "3", is_mirror: "True", is_ascent: "False", ascensionist_count: "40", quality_average: "2.0" },
    // Bravo: project at 30, no community data at all.
    { climb_uuid: "b", climb_name: "Bravo", date: "2024-03-01 10:00:00", angle: "30", logged_grade: "7a/V6", tries: "4", is_mirror: "False", is_ascent: "False" },
    // Charlie: flashed at 50; community sends but no rating.
    { climb_uuid: "c", climb_name: "Charlie", date: "2024-01-15 10:00:00", angle: "50", logged_grade: "6a/V3", tries: "1", is_mirror: "False", is_ascent: "True", ascensionist_count: "5" },
    // Delta: ungraded send at 40.
    { climb_uuid: "d", climb_name: "Delta", date: "2023-12-01 10:00:00", angle: "40", logged_grade: "", tries: "1", is_mirror: "False", is_ascent: "True", ascensionist_count: "9", quality_average: "1.5" },
  ]);
  state.rows = rows;
  state.groupClimbVariants = true;
  state.climbs = summarizeClimbs(rows);
  state.climbAngles = new Set();
  state.climbFilter = "all";
  state.climbQuery = "";

  const names = () => filteredClimbs().map((climb) => climb.name);
  const sorted = (column, direction) => {
    state.climbSort = column;
    state.climbSortDirection = direction;
    return names();
  };
  const out = {};
  out.columns = Object.keys(CLIMB_COLUMNS);
  out.defaults = Object.fromEntries(
    Object.entries(CLIMB_COLUMNS).map(([column, spec]) => [column, spec.defaultDirection]),
  );
  out.recentDesc = sorted("recent", "desc");
  out.recentAsc = sorted("recent", "asc");
  out.qualityDesc = sorted("quality", "desc");
  out.qualityAsc = sorted("quality", "asc");
  out.firstSendDesc = sorted("firstSend", "desc");
  out.firstSendAsc = sorted("firstSend", "asc");
  out.gradeDesc = sorted("grade", "desc");
  out.gradeAsc = sorted("grade", "asc");
  out.angleAsc = sorted("angle", "asc");
  out.nameDesc = sorted("name", "desc");
  out.loadedAngles = loadedAngles();

  state.climbSort = "name";
  state.climbSortDirection = "asc";
  state.climbAngles = new Set([45]);
  out.grouped45 = names();
  out.alphaChips = angleVariantsHtml(filteredClimbs()[0]);
  state.groupClimbVariants = false;
  state.climbs = summarizeClimbs(rows);
  out.ungrouped45 = filteredClimbs().map((climb) => climb.name + "|" + (climb.mirror ? "mirror" : "original") + "|" + climb.angle);
  state.climbAngles = new Set([40, 50]);
  out.ungrouped40or50 = filteredClimbs().map((climb) => climb.name + "|" + climb.angle);
  state.climbAngles = new Set();
  const sentOriginal = { mirror: false, angle: 40, sends: 3 };
  const triedMirror = { mirror: true, angle: 40, sends: 0 };
  const sentMirror = { mirror: true, angle: 40, sends: 1 };
  out.originalChip = angleChipHtml({ angle: 40, original: sentOriginal, mirror: null });
  out.bothWaysChip = angleChipHtml({ angle: 40, original: sentOriginal, mirror: sentMirror });
  out.mixedChip = angleChipHtml({ angle: 40, original: sentOriginal, mirror: triedMirror });
  out.projectChip = angleChipHtml({ angle: 40, original: null, mirror: triedMirror });
  out.decorativeChip = variantChipHtml(sentMirror, { decorative: true });
  out.legend = angleLegendHtml();
  out.stars = starsHtml(2.7);
  out.starsMax = starsHtml(3);
  return out;
})()
`;
process.stdout.write(JSON.stringify(vm.runInContext(probe, sandbox)));
"""


@pytest.fixture(scope="module")
def app_js_probe(tmp_path_factory):
    if NODE is None:
        pytest.skip("node is needed to run docs/app.js")
    harness = tmp_path_factory.mktemp("app_js") / "harness.js"
    harness.write_text(APP_JS_HARNESS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(harness), str(DOCS_APP)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def test_every_column_sorts_both_ways_with_nulls_last(app_js_probe):
    assert app_js_probe["columns"] == SORT_COLUMNS
    # Text columns open A-Z; everything else opens with the biggest/newest first.
    assert app_js_probe["defaults"] == {
        "name": "asc",
        "grade": "desc",
        "angle": "asc",
        "sends": "desc",
        "tries": "desc",
        "sessions": "desc",
        "firstSend": "desc",
        "recent": "desc",
        "communitySends": "desc",
        "quality": "desc",
    }
    assert app_js_probe["recentDesc"] == ["Bravo", "Alpha", "Charlie", "Delta"]
    assert app_js_probe["recentAsc"] == ["Delta", "Charlie", "Alpha", "Bravo"]
    # Bravo/Charlie have no rating: last in both directions, tie broken by name.
    assert app_js_probe["qualityDesc"] == ["Alpha", "Delta", "Bravo", "Charlie"]
    assert app_js_probe["qualityAsc"] == ["Delta", "Alpha", "Bravo", "Charlie"]
    # Bravo is a project (no first send): last in both directions.
    assert app_js_probe["firstSendDesc"] == ["Charlie", "Alpha", "Delta", "Bravo"]
    assert app_js_probe["firstSendAsc"] == ["Delta", "Alpha", "Charlie", "Bravo"]
    # Delta's grade can't be parsed: last in both directions.
    assert app_js_probe["gradeDesc"] == ["Bravo", "Alpha", "Charlie", "Delta"]
    assert app_js_probe["gradeAsc"] == ["Charlie", "Alpha", "Bravo", "Delta"]
    # Grouped angle sort uses the lowest angle in the group, then name.
    assert app_js_probe["angleAsc"] == ["Bravo", "Alpha", "Delta", "Charlie"]
    assert app_js_probe["nameDesc"] == ["Delta", "Charlie", "Bravo", "Alpha"]


def _strokes(chip):
    """(original, mirror) stroke states of a chip's check mark: lit/ghost/off."""
    states = []
    for name in ("stroke-original", "stroke-mirror"):
        path = chip.find("path", class_=name)
        states.append(next(cls[3:] for cls in path["class"] if cls.startswith("is-")))
    return tuple(states)


def test_angle_filter_matches_any_grouped_variant(app_js_probe):
    assert app_js_probe["loadedAngles"] == [30, 40, 45, 50]
    # Grouped: Alpha stays because its mirror variant is at 45; its 40 chip dims.
    assert app_js_probe["grouped45"] == ["Alpha"]
    chips = BeautifulSoup(app_js_probe["alphaChips"], "html.parser").find_all(class_="angle-chip")
    assert [chip.get_text(strip=True) for chip in chips] == ["40°", "45°"]
    assert "is-dimmed" in chips[0]["class"] and "is-sent" in chips[0]["class"]
    assert _strokes(chips[0]) == ("lit", "off")
    assert chips[0]["aria-label"] == "40 degrees: original sent once; outside the angle filter"
    assert "is-dimmed" not in chips[1]["class"] and "is-project" in chips[1]["class"]
    assert _strokes(chips[1]) == ("off", "ghost")
    assert chips[1]["aria-label"] == "45 degrees: mirror tried, not sent (project)"
    # Ungrouped: only the variant at a selected angle is a row.
    assert app_js_probe["ungrouped45"] == ["Alpha|mirror|45"]
    assert app_js_probe["ungrouped40or50"] == ["Alpha|40", "Charlie|50", "Delta|40"]


def test_angle_chips_use_the_apps_two_stroke_check(app_js_probe):
    def chip(key):
        return BeautifulSoup(app_js_probe[key], "html.parser").find(class_="angle-chip")

    original = chip("originalChip")
    assert original["role"] == "img"
    assert original["aria-label"] == original["title"] == "40 degrees: original sent 3 times"
    assert "is-sent" in original["class"]
    assert _strokes(original) == ("lit", "off")

    # Sent both ways lights the full check.
    both = chip("bothWaysChip")
    assert _strokes(both) == ("lit", "lit")
    assert both["aria-label"] == "40 degrees: original sent 3 times; mirror sent once"

    # A tried-but-unsent orientation is a faint stroke, not a lit one.
    mixed = chip("mixedChip")
    assert _strokes(mixed) == ("lit", "ghost")
    assert "is-sent" in mixed["class"]
    assert mixed["aria-label"] == "40 degrees: original sent 3 times; mirror tried, not sent"

    # No send in either orientation: outlined project chip, no lit stroke.
    project = chip("projectChip")
    assert "is-project" in project["class"]
    assert _strokes(project) == ("off", "ghost")
    assert project["aria-label"] == "40 degrees: mirror tried, not sent (project)"

    # Chips beside a heading that already names the variant are decorative.
    decorative = chip("decorativeChip")
    assert decorative["aria-hidden"] == "true"
    assert not decorative.has_attr("role")
    assert _strokes(decorative) == ("off", "lit")

    # The SVG never references anything external (CSP: no external assets).
    for key in ("originalChip", "projectChip"):
        assert "http" not in app_js_probe[key]
        assert "<script" not in app_js_probe[key]


def test_angle_legend_explains_the_marks_in_the_apps_terms(app_js_probe):
    legend = BeautifulSoup(app_js_probe["legend"], "html.parser")
    items = legend.find_all(class_="legend-item")
    meanings = [item.find(string=True, recursive=False).strip() for item in items]
    assert meanings == [
        "original sent",
        "mirror sent",
        "sent both ways",
        "project (tried, not sent)",
        "outside the angle filter",
    ]
    chips = [item.find(class_="angle-chip") for item in items]
    assert [_strokes(chip) for chip in chips[:4]] == [
        ("lit", "off"),
        ("off", "lit"),
        ("lit", "lit"),
        ("ghost", "off"),
    ]
    assert ["is-sent" in chip["class"] for chip in chips[:4]] == [True, True, True, False]
    # The dimmed explanation only appears once an angle filter is active.
    assert items[4].get("id") == "angleLegendDimmed"
    assert "is-hidden" in items[4]["class"]
    assert "is-dimmed" in chips[4]["class"]


def test_community_stars_render_on_the_apps_three_star_scale(app_js_probe):
    stars = BeautifulSoup(app_js_probe["stars"], "html.parser").find(class_="stars")
    assert stars["title"] == "2.7 of 3 stars"
    assert stars.find(class_="stars-track").get("aria-hidden") == "true"
    assert stars.find(class_="stars-fill")["style"] == "width:90%"
    assert stars.find(class_="stars-value").get_text(strip=True) == "2.7"

    full = BeautifulSoup(app_js_probe["starsMax"], "html.parser").find(class_="stars")
    assert full.find(class_="stars-fill")["style"] == "width:100%"
    assert full.find(class_="stars-value").get_text(strip=True) == "3.0"
