#!/usr/bin/env python3
"""Headless smoke test for the BoardLog Lambda backend.

The Function URL is public (AuthType NONE); access is gated server-side. The
page's whole login is the gate phrase: a correct knock returns a short-lived
session token, and exports present that token. Scripts can still send the gate
phrase and access key together as headers. This hits the URL exactly like the
browser does and checks the responses:

  - unlock with the correct gate phrase       -> expects 200 with a session token
  - unlock with a wrong gate phrase           -> expects 403
  - export with a forged session token        -> expects 403
  - export with the session, empty creds      -> expects 400 (auth passed, runtime healthy)
  - export with a wrong access key            -> expects 403   (only with --access-key)
  - export with both secrets, empty creds     -> expects 400   (only with --access-key)

The endpoint is read from docs/site.config.js unless overridden. Secrets come
from flags or env vars and are never written anywhere.

Examples:
  python scripts/smoke_test.py --gate "moonboard-at-midnight"
  python scripts/smoke_test.py --gate "..." --access-key "..."   # also checks the scripts path
  python scripts/smoke_test.py --full --gate "..." \
      --username you --password ****     # also runs a real export via the session

Exit code is non-zero if any expected check fails.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE_CONFIG = REPO_ROOT / "docs" / "site.config.js"


def site_endpoint() -> str:
    text = SITE_CONFIG.read_text(encoding="utf-8")
    match = re.search(r"defaultEndpoint\s*:\s*\"([^\"]*)\"", text)
    return match.group(1) if match else ""


def post(endpoint: str, body: dict, headers: dict) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=data, headers={"Content-Type": "application/json", **headers}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as error:
        return 0, f"request failed: {error}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the BoardLog Lambda.")
    parser.add_argument("--endpoint", default=site_endpoint())
    parser.add_argument("--gate", default=os.environ.get("BOARDLOG_GATE_PHRASE", ""))
    parser.add_argument(
        "--access-key",
        default=os.environ.get("BOARDLOG_ACCESS_KEY", ""),
        help="Optional; also exercises the scripts path (gate phrase + access key headers).",
    )
    parser.add_argument("--full", action="store_true", help="Also run a real export (needs --username/--password).")
    parser.add_argument("--username", default=os.environ.get("TENSION_USERNAME", ""))
    parser.add_argument("--password", default=os.environ.get("TENSION_PASSWORD", ""))
    args = parser.parse_args()

    if not args.endpoint:
        print("No endpoint (set defaultEndpoint in docs/site.config.js or pass --endpoint).")
        return 2
    if not args.gate:
        print("Provide --gate (or BOARDLOG_GATE_PHRASE).")
        return 2

    print(f"endpoint = {args.endpoint}\n")
    failures = 0

    def check(label: str, expected: int, body: dict, headers: dict, *, require=None) -> str:
        nonlocal failures
        status, text = post(args.endpoint, body, headers)
        ok = status == expected and (require is None or require(text))
        failures += 0 if ok else 1
        # Never echo a session token back to the terminal.
        shown = re.sub(r'"session":\s*"[^"]*"', '"session": "<redacted>"', text)
        snippet = shown if len(shown) < 160 else shown[:157] + "..."
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: HTTP {status} (expected {expected}) {snippet}")
        return text

    def has_session(text: str) -> bool:
        try:
            return bool(json.loads(text).get("session"))
        except ValueError:
            return False

    unlock_text = check(
        "unlock, correct gate (issues a session)", 200, {"action": "unlock"}, {"X-Board-Gate": args.gate}, require=has_session
    )
    session = json.loads(unlock_text).get("session", "") if has_session(unlock_text) else ""

    check("unlock, wrong gate", 403, {"action": "unlock"}, {"X-Board-Gate": "definitely-wrong"})
    check(
        "export, forged session token",
        403,
        {"board": "tension", "username": "x", "password": "y"},
        {"X-Board-Session": "9999999999.deadbeef"},
    )
    if session:
        check(
            "export, session ok, empty creds",
            400,
            {"board": "tension", "username": "", "password": ""},
            {"X-Board-Session": session},
        )
    else:
        failures += 1
        print("[FAIL] export via session: skipped, no session token was issued")

    if args.access_key:
        check(
            "export, wrong access key",
            403,
            {"board": "tension", "username": "x", "password": "y"},
            {"X-Board-Gate": args.gate, "X-Board-Room-Key": "definitely-wrong"},
        )
        check(
            "export, both secrets ok, empty creds",
            400,
            {"board": "tension", "username": "", "password": ""},
            {"X-Board-Gate": args.gate, "X-Board-Room-Key": args.access_key},
        )

    if args.full:
        if not args.username or not args.password:
            print("\n--full requires --username and --password.")
            return 2
        status, text = post(
            args.endpoint,
            {"board": "tension", "username": args.username, "password": args.password},
            {"X-Board-Session": session},
        )
        ok = status == 200
        failures += 0 if ok else 1
        rows = json.loads(text).get("row_count", "?") if ok else "-"
        print(f"[{'PASS' if ok else 'FAIL'}] full export via session: HTTP {status} (expected 200) row_count={rows}")

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
