"""Live Tension API integration tests.

Run with credentials present:

    pytest tests/integration -v

Skipped automatically when TENSION_USERNAME / TENSION_PASSWORD are not set.
These tests exercise the exact code path the Lambda backend runs in production:
login -> download/sync the shared database -> build the logbook DataFrame.
"""

from __future__ import annotations

import json

import pytest

import boardlib.api.aurora as aurora
import backend.boardlog_lambda.handler as handler


pytestmark = pytest.mark.integration


def test_login_returns_token(board, tension_credentials):
    username, password = tension_credentials
    session = aurora.login(board, username, password)
    assert isinstance(session, dict)
    assert session.get("token"), "login response did not include a token"


def test_login_rejects_bad_password(board, tension_credentials):
    username, _ = tension_credentials
    with pytest.raises(ValueError):
        aurora.login(board, username, "definitely-not-the-password")


def test_export_logbook_end_to_end(board, tension_credentials, tmp_path, monkeypatch):
    """Reproduce the Lambda's real work: login, sync DB, build logbook rows."""
    username, password = tension_credentials
    monkeypatch.setenv("BOARDLOG_CACHE_DIR", str(tmp_path))

    rows = handler.export_logbook(board, username, password)

    assert isinstance(rows, list)
    # Rows are JSON-serializable dicts shaped like the documented schema.
    json.dumps(rows)
    if rows:
        first = rows[0]
        for field in ("board", "angle", "climb_name", "date"):
            assert field in first, f"missing field {field!r} in export row"


def test_lambda_handler_happy_path(board, tension_credentials, tmp_path, monkeypatch):
    """Full handler invocation, matching what the Function URL delivers."""
    username, password = tension_credentials
    monkeypatch.setenv("BOARDLOG_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("BOARDLOG_ACCESS_KEY", raising=False)

    event = {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"content-type": "application/json"},
        "body": json.dumps(
            {"board": board, "username": username, "password": password}
        ),
    }

    response = handler.lambda_handler(event, None)

    assert response["statusCode"] == 200, response["body"]
    payload = json.loads(response["body"])
    assert payload["board"] == board
    assert payload["row_count"] == len(payload["rows"])
    assert isinstance(payload["rows"], list)
