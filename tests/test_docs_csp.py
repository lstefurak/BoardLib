import csv
from collections import defaultdict
from pathlib import Path

from bs4 import BeautifulSoup


DOCS_INDEX = Path(__file__).resolve().parents[1] / "docs" / "index.html"
DOCS_APP = Path(__file__).resolve().parents[1] / "docs" / "app.js"
SAMPLE_CSV = Path(__file__).resolve().parents[1] / "data" / "tension-logbook.csv"


def test_csp_allows_lambda_function_urls_in_any_region():
    html = DOCS_INDEX.read_text(encoding="utf-8")

    assert "connect-src https://*.on.aws" in html
    assert "connect-src https://*.lambda-url.us-east-1.on.aws" not in html


def test_endpoint_placeholder_is_region_neutral():
    html = DOCS_INDEX.read_text(encoding="utf-8")

    assert "https://abc.lambda-url.region.on.aws/" in html
    assert "placeholder=\"https://abc.lambda-url.us-east-1.on.aws/\"" not in html


def test_docs_keep_csp_safe_script_surface():
    html = DOCS_INDEX.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    assert [script["src"] for script in soup.find_all("script")] == ["site.config.js", "app.js"]
    assert "http://" not in html


def test_access_key_is_not_part_of_tension_credential_form():
    html = DOCS_INDEX.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    api_form = soup.find("form", id="apiForm")
    assert api_form is not None

    inputs = {input_tag.get("id"): input_tag for input_tag in api_form.find_all("input")}
    assert set(inputs) == {"usernameInput", "passwordInput", "endpointInput"}
    assert inputs["usernameInput"].get("autocomplete") == "username"
    assert inputs["passwordInput"].get("autocomplete") == "current-password"
    assert soup.find("input", id="keyInput").find_parent("form").get("id") == "keyForm"


def test_reveal_toggles_are_static_input_adornments():
    html = DOCS_INDEX.read_text(encoding="utf-8")
    js = DOCS_APP.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    assert "insertAdjacentElement" not in js
    assert "document.createElement(\"button\")" not in js
    for toggle in soup.select("[data-reveal-for]"):
        assert "secret-field" in toggle.find_parent().get("class", [])


def test_sample_csv_has_sessions_for_no_backend_smoke_path():
    with SAMPLE_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    sessions = defaultdict(list)
    for row in rows:
        if row.get("date") and row.get("climb_name"):
            sessions[row["date"][:10]].append(row)

    assert rows
    assert sessions
    assert any(row.get("is_ascent", "").lower() == "true" for row in rows)
