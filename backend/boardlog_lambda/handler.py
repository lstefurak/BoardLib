from __future__ import annotations

import json
import os
import pathlib
import sys
import traceback
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import boardlib.api.aurora
import boardlib.db.aurora


DEFAULT_ALLOWED_BOARDS = {"tension"}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", event.get("httpMethod", ""))
    if method == "OPTIONS":
        return response(204, "")
    if method != "POST":
        return response(405, {"error": "Method not allowed"})

    if not authorized(event):
        return response(403, {"error": "Not invited"})

    try:
        body = parse_body(event)
        board = str(body.get("board", "tension")).strip().lower()
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))

        if board not in allowed_boards():
            return response(400, {"error": f"Unsupported board: {board}"})
        if not username or not password:
            return response(400, {"error": "Username and password are required"})

        rows = export_logbook(board, username, password)
        return response(
            200,
            {
                "board": board,
                "row_count": len(rows),
                "rows": rows,
            },
        )
    except ValueError as error:
        return response(400, {"error": str(error)})
    except Exception as error:
        print("BoardLog export failed:", type(error).__name__, str(error))
        print(traceback.format_exc())
        return response(502, {"error": "Export failed"})


def parse_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raise ValueError("Base64 request bodies are not supported")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("JSON body must be an object")
    return parsed


def authorized(event: dict[str, Any]) -> bool:
    expected = os.environ.get("BOARDLOG_ACCESS_KEY")
    if not expected:
        return True

    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    provided = headers.get("x-board-room-key", "")
    return provided == expected


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

    token = boardlib.api.aurora.login(board, username, password)["token"]
    max_pages = int(os.environ.get("BOARDLOG_MAX_SYNC_PAGES", "100"))
    tables_and_sync_dates = boardlib.db.aurora.get_shared_syncs(db_path)
    for sync_result in boardlib.api.aurora.sync(
        board,
        tables_and_sync_dates,
        token=token,
        max_pages=max_pages,
    ):
        boardlib.db.aurora.sync_shared_tables(db_path, sync_result)
    return token


def response(status_code: int, body: Any) -> dict[str, Any]:
    headers = cors_headers()
    if body == "":
        return {"statusCode": status_code, "headers": headers, "body": ""}
    headers["Content-Type"] = "application/json"
    return {
        "statusCode": status_code,
        "headers": headers,
        "body": json.dumps(body),
    }


def cors_headers() -> dict[str, str]:
    origin = os.environ.get("ALLOWED_ORIGIN", "*")
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type,X-Board-Room-Key",
        "Access-Control-Allow-Methods": "OPTIONS,POST",
        "Vary": "Origin",
    }
