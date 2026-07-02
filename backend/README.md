# BoardLog Lambda Backend

This backend is designed for an AWS Lambda Function URL. The GitHub Pages UI sends Tension credentials to this function over HTTPS. The function logs in to the Tension API, builds the logbook using BoardLib, returns JSON rows, and does not store the password.

## Request

`POST /`

Headers:

- `Content-Type: application/json`
- `X-Board-Gate: <gate phrase>` if a gate phrase is configured
- `X-Board-Room-Key: <access key>` if an access key is configured

Both secrets are independent and verified server-side with a constant-time
compare. A `POST` body of `{"action": "unlock"}` with a valid `X-Board-Gate`
header returns `{"ok": true}` and is used by the page's gate button; the export
path additionally requires `X-Board-Room-Key`.

Body:

```json
{
  "board": "tension",
  "username": "your_tension_username",
  "password": "your_tension_password"
}
```

## Response

```json
{
  "board": "tension",
  "row_count": 270,
  "rows": [
    {
      "board": "tension",
      "angle": 45,
      "climb_name": "Captain Progression",
      "date": "2019-02-03T15:25:02",
      "logged_grade": "6c/V5",
      "displayed_grade": "6c/V5",
      "is_benchmark": true,
      "tries": 1,
      "is_mirror": false,
      "sessions_count": 1,
      "tries_total": 1,
      "is_repeat": false,
      "is_ascent": true,
      "comment": "",
      "climb_uuid": "69d9e7c4-example",
      "ascensionist_count": 320,
      "quality_average": 3.6
    }
  ]
}
```

## Environment Variables

- `BOARDLOG_ACCESS_KEY_PARAM`: SSM SecureString parameter name holding the access key (production). Read at runtime and decrypted.
- `BOARDLOG_GATE_PHRASE_PARAM`: SSM SecureString parameter name holding the gate phrase (production).
- `BOARDLOG_ACCESS_KEY` / `BOARDLOG_GATE_PHRASE`: Plaintext fallbacks for local/dev/tests. Take precedence over the SSM parameters when set. Locally, a check is disabled if neither the env var nor the parameter is configured. **In Lambda the same situation fails closed** (every request is refused with 403 and a warning is logged) so a misconfigured deployment can never silently run without authentication.
- `BOARDLOG_CACHE_DIR`: Optional database cache directory. Defaults to `/tmp/boardlog`.
- `BOARDLOG_MAX_SYNC_PAGES`: Optional shared database sync page cap. Defaults to `100`.
- `BOARDLOG_ALLOWED_BOARDS`: Optional comma-separated board names. Defaults to `tension`.

## Deployment Sketch

1. Create a Lambda function using Python 3.13 or another Python runtime supported by your AWS account.
2. Package this repository plus dependencies into the Lambda deployment artifact.
3. Set the handler to `backend.boardlog_lambda.handler.lambda_handler`.
4. Configure the environment variables above.
5. Create a Lambda Function URL with CORS enabled for your GitHub Pages origin.
6. Put the Function URL in `docs/site.config.js` as `defaultEndpoint`.

Avoid enabling request body logging. Tension passwords are intentionally only used in memory for one request.
