import csv
import re
from collections import defaultdict
from pathlib import Path

from bs4 import BeautifulSoup


DOCS_INDEX = Path(__file__).resolve().parents[1] / "docs" / "index.html"
DOCS_APP = Path(__file__).resolve().parents[1] / "docs" / "app.js"
SAMPLE_CSV = Path(__file__).resolve().parent / "fixtures" / "tension-logbook-sample.csv"


def test_csp_allows_lambda_function_urls_in_any_region():
    html = DOCS_INDEX.read_text(encoding="utf-8")

    assert "connect-src https://*.on.aws" in html
    assert "connect-src https://*.lambda-url.us-east-1.on.aws" not in html


def test_page_ships_endpoint_from_config_not_a_form_field():
    # The Function URL comes from site.config.js (or the local-dev override);
    # there is no endpoint input for the user to fill in.
    html = DOCS_INDEX.read_text(encoding="utf-8")
    js = DOCS_APP.read_text(encoding="utf-8")

    assert "endpointInput" not in html
    assert "endpointInput" not in js
    assert "config.defaultEndpoint" in js


def test_docs_keep_csp_safe_script_surface():
    html = DOCS_INDEX.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    # Every script is a same-origin relative file (CSP script-src 'self'); no
    # remote or inline scripts. site.config.local.js is an optional local-dev
    # override that 404s harmlessly in production.
    scripts = soup.find_all("script")
    assert [script.get("src") for script in scripts] == [
        "site.config.js",
        "site.config.local.js",
        "app.js",
    ]
    assert all(not (script.string or "").strip() for script in scripts)
    assert "http://" not in html


def test_docs_have_no_inline_event_handlers():
    # Inline on*= handlers would require 'unsafe-inline' in script-src; the CSP
    # forbids them, so none may sneak into the markup.
    html = DOCS_INDEX.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(True):
        assert not [attr for attr in tag.attrs if attr.lower().startswith("on")]


def test_credential_form_leads_with_username_and_password():
    html = DOCS_INDEX.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    api_form = soup.find("form", id="apiForm")
    assert api_form is not None

    inputs = {input_tag.get("id"): input_tag for input_tag in api_form.find_all("input")}
    # The board credentials are the only fields, so password keepers see one
    # clean username/password pair.
    assert set(inputs) == {"usernameInput", "passwordInput"}
    assert inputs["usernameInput"].get("autocomplete") == "username"
    assert inputs["passwordInput"].get("autocomplete") == "current-password"


def test_page_only_ever_holds_a_session_token():
    # One knock is the whole login. The page exchanges it for a session token
    # and never stores the gate phrase or handles the backend access key.
    html = DOCS_INDEX.read_text(encoding="utf-8")
    js = DOCS_APP.read_text(encoding="utf-8")

    assert "accessKeyInput" not in html
    assert "roomAccess" not in html
    assert "X-Board-Session" in js
    assert "X-Board-Room-Key" not in js
    assert "boardlog:gate" not in js
    assert "boardlog:key" not in js
    # The knock is sent to the backend exactly once, at the gate.
    assert js.count('"X-Board-Gate"') == 1


def test_localhost_dev_path_does_not_call_production():
    # On localhost the page must not fall back to the shipped Function URL, and
    # a cross-origin CORS failure at the gate must open the door rather than
    # trap the tester (see scripts/serve_local.py).
    js = DOCS_APP.read_text(encoding="utf-8")

    assert "IS_LOCAL" in js
    assert re.search(r"if\s*\(IS_LOCAL\)\s*return", js)


def test_export_csv_neutralizes_formula_leading_cells():
    # Export CSV must defuse formula-leading cells (=, +, -, @, tab, CR) so a
    # board/user-authored string can't run as a formula when the download is
    # opened in Excel/Sheets.
    js = DOCS_APP.read_text(encoding="utf-8")

    assert r"/^[=+\-@\t\r]/" in js
    assert "'${text}" in js


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
