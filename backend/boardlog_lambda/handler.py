from __future__ import annotations

import base64
import hmac
import json
import os
import pathlib
import sys
import traceback
from typing import Any

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import boardlib.api.aurora
import boardlib.db.aurora


DEFAULT_ALLOWED_BOARDS = {"tension"}

GATE_HEADER = "x-board-gate"
ACCESS_KEY_HEADER = "x-board-room-key"

# Two independent secrets, each resolved from a direct env var (local/dev/tests)
# or an SSM SecureString named by the *_PARAM env var (production). Keeping them
# separate lets the gate phrase and the access key be rotated independently.
GATE_SECRET = ("BOARDLOG_GATE_PHRASE", "BOARDLOG_GATE_PHRASE_PARAM")
ACCESS_SECRET = ("BOARDLOG_ACCESS_KEY", "BOARDLOG_ACCESS_KEY_PARAM")

_secret_cache: dict[str, str] = {}

DEFAULT_MAX_SYNC_PAGES = 100


class BoardLoginError(Exception):
    """The board API rejected the user's credentials."""


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", event.get("httpMethod", ""))
    if method == "OPTIONS":
        return response(204, "")
    if method != "POST":
        return log_and_respond(405, {"error": "Method not allowed"})

    try:
        body = parse_body(event)
    except ValueError as error:
        return log_and_respond(400, {"error": str(error)})

    action = str(body.get("action", "")).strip().lower()
    board = str(body.get("board", "tension")).strip().lower()
    username = str(body.get("username", "")).strip()
    log_action = "unlock" if action == "unlock" else "export"

    try:
        # The gate "Enter" button verifies only the gate phrase, server-side.
        if action == "unlock":
            if check_secret(event, GATE_HEADER, GATE_SECRET):
                return log_and_respond(200, {"ok": True}, action="unlock")
            return log_and_respond(403, {"error": "The door stays shut."}, action="unlock")

        # The export path requires both independent secrets. Evaluate both and
        # return one generic error so the response doesn't reveal which secret
        # was wrong.
        gate_ok = check_secret(event, GATE_HEADER, GATE_SECRET)
        key_ok = check_secret(event, ACCESS_KEY_HEADER, ACCESS_SECRET)
        if not (gate_ok and key_ok):
            return log_and_respond(403, {"error": "Not authorized"}, action="export", username=username, board=board)

        password = str(body.get("password", ""))

        if board not in allowed_boards():
            return log_and_respond(400, {"error": f"Unsupported board: {board}"}, action="export", username=username, board=board)
        if not username or not password:
            return log_and_respond(400, {"error": "Username and password are required"}, action="export", username=username, board=board)

        rows = export_logbook(board, username, password)
        return log_and_respond(
            200,
            {
                "board": board,
                "row_count": len(rows),
                "rows": rows,
            },
            action="export",
            username=username,
            board=board,
            row_count=len(rows),
        )
    except BoardLoginError as error:
        return log_and_respond(401, {"error": str(error)}, action=log_action, username=username, board=board)
    except Exception as error:
        # Anything else is a server-side fault. The detail goes to the log only;
        # the client gets a generic message so internals never leak.
        print("BoardLog request failed:", type(error).__name__, str(error))
        print(traceback.format_exc())
        return log_and_respond(
            502,
            {"error": "Gate check failed" if log_action == "unlock" else "Export failed"},
            action=log_action,
            username=username,
            board=board,
        )


def parse_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        # Function URLs base64-encode the body for any Content-Type not on
        # their text allowlist (e.g. curl's default form encoding).
        try:
            body = base64.b64decode(body).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("Request body is not valid base64-encoded UTF-8") from error
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError("Request body is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError("JSON body must be an object")
    return parsed


def running_in_lambda() -> bool:
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def check_secret(event: dict[str, Any], header: str, secret_source: tuple[str, str]) -> bool:
    """Constant-time check of a request header against a configured secret.

    Locally (tests/dev) a check whose secret is not configured is disabled.
    In Lambda, where the Function URL is public, an unconfigured secret is
    treated as a deployment error and the check FAILS CLOSED — otherwise a
    typo in one env var name would silently turn off authentication.
    """
    expected = resolve_secret(*secret_source)
    if not expected:
        if running_in_lambda():
            print(
                f"WARNING: secret for header '{header}' is not configured "
                f"(set {secret_source[0]} or {secret_source[1]}); refusing request"
            )
            return False
        return True
    provided = request_header(event, header)
    # Encode to bytes: hmac.compare_digest raises TypeError on non-ASCII str,
    # which would turn a non-ASCII passphrase into a 502 for every request.
    return hmac.compare_digest(str(provided).encode("utf-8"), str(expected).encode("utf-8"))


def resolve_secret(plain_env: str, param_env: str) -> str | None:
    """Resolve a secret from a direct env var, else an SSM SecureString.

    ``plain_env`` is read first so local runs and tests can set the value
    directly. In production only ``param_env`` (the SSM parameter name) is set,
    and the decrypted value is fetched once and cached for the warm container.
    """
    direct = os.environ.get(plain_env)
    if direct:
        return direct

    param_name = os.environ.get(param_env)
    if not param_name:
        return None

    if param_name not in _secret_cache:
        import boto3  # provided by the Lambda runtime; not bundled

        client = boto3.client("ssm")
        value = client.get_parameter(Name=param_name, WithDecryption=True)
        _secret_cache[param_name] = value["Parameter"]["Value"]
    return _secret_cache[param_name]


def request_header(event: dict[str, Any], name: str) -> str:
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    return headers.get(name.lower(), "") or ""


def allowed_boards() -> set[str]:
    configured = os.environ.get("BOARDLOG_ALLOWED_BOARDS")
    if not configured:
        return DEFAULT_ALLOWED_BOARDS
    return {board.strip().lower() for board in configured.split(",") if board.strip()}


def export_logbook(board: str, username: str, password: str) -> list[dict[str, Any]]:
    db_path = database_path(board)
    token = ensure_database(board, db_path, username, password)
    frame = boardlib.api.aurora.logbook_entries(board, token, db_path)
    payload = frame.to_json(orient="records", date_format="iso")
    return json.loads(payload)


def database_path(board: str) -> pathlib.Path:
    cache_dir = pathlib.Path(os.environ.get("BOARDLOG_CACHE_DIR", "/tmp/boardlog"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{board}.db"


def ensure_database(board: str, db_path: pathlib.Path, username: str, password: str) -> str:
    if not db_path.exists():
        boardlib.db.aurora.download_database(board, db_path)

    try:
        token = boardlib.api.aurora.login(board, username, password)["token"]
    except ValueError as error:
        # aurora.login raises ValueError on 422 (invalid credentials).
        raise BoardLoginError(str(error)) from error
    except requests.exceptions.HTTPError as error:
        status = getattr(error.response, "status_code", None)
        if status in (400, 401, 403):
            raise BoardLoginError("Board login failed; check your username and password") from error
        raise
    boardlib.api.aurora.sync_local_database(board, db_path, token, max_pages=max_sync_pages())
    return token


def max_sync_pages() -> int:
    configured = os.environ.get("BOARDLOG_MAX_SYNC_PAGES", "")
    try:
        return int(configured)
    except ValueError:
        if configured:
            print(f"WARNING: BOARDLOG_MAX_SYNC_PAGES={configured!r} is not an integer; using {DEFAULT_MAX_SYNC_PAGES}")
        return DEFAULT_MAX_SYNC_PAGES


def log_and_respond(
    status_code: int,
    body: Any,
    *,
    action: str = "",
    username: str = "",
    board: str = "",
    row_count: int | None = None,
) -> dict[str, Any]:
    """Emit one structured request log line, then return the HTTP response.

    The line is JSON so CloudWatch metric filters and Logs Insights can break
    invocations down by outcome and by username. The password and the gate /
    access secrets are never included.
    """
    print(
        json.dumps(
            {
                "type": "boardlog_request",
                "status": status_code,
                "ok": status_code == 200,
                "action": action or None,
                "username": username or None,
                "board": board or None,
                "row_count": row_count,
            }
        )
    )
    return response(status_code, body)


def response(status_code: int, body: Any) -> dict[str, Any]:
    # CORS headers are added by the Lambda Function URL's own CORS config. The
    # handler must NOT also emit Access-Control-* headers, or the browser sees
    # duplicate Access-Control-Allow-Origin values and blocks the response.
    if body == "":
        return {"statusCode": status_code, "headers": {}, "body": ""}
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
