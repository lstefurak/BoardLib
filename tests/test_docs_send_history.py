"""Structural checks for the Climbs (send history) view in the docs app."""

from pathlib import Path

from bs4 import BeautifulSoup


DOCS_INDEX = Path(__file__).resolve().parents[1] / "docs" / "index.html"
DOCS_APP = Path(__file__).resolve().parents[1] / "docs" / "app.js"


def _soup():
    return BeautifulSoup(DOCS_INDEX.read_text(encoding="utf-8"), "html.parser")


def test_dashboard_has_view_switcher():
    soup = _soup()

    tablist = soup.find(attrs={"role": "tablist"})
    assert tablist is not None
    tabs = tablist.find_all("button", attrs={"role": "tab"})
    assert [tab.get("id") for tab in tabs] == ["viewSessionsTab", "viewClimbsTab"]
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
    assert climbs_view.find(id="climbSort") is not None
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

    table = climbs_view.find(id="climbHistoryTable")
    assert table is not None
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    assert headers == [
        "Climb",
        "Grade",
        "Angle",
        "Sends",
        "Tries",
        "Sessions",
        "First send",
        "Last climbed",
        "Community",
    ]
    assert climbs_view.find(id="climbHistoryRows") is not None


def test_climb_rendering_escapes_board_authored_content():
    # Climb names, grades, and comments are authored by other board users, so
    # every interpolation in the climbs renderers must go through escapeHtml.
    js = DOCS_APP.read_text(encoding="utf-8")

    assert "escapeHtml(climb.name)" in js
    assert "escapeHtml(climb.grade)" in js
    assert "escapeHtml(entry.comment)" in js
    assert "escapeHtml(entry.logged_grade)" in js
