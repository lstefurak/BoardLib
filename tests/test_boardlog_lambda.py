import json
import os
import time
import unittest
import unittest.mock

import backend.boardlog_lambda.handler as handler


class TestBoardLogLambda(unittest.TestCase):
    def setUp(self):
        for name in (
            "BOARDLOG_ACCESS_KEY",
            "BOARDLOG_GATE_PHRASE",
            "BOARDLOG_ACCESS_KEY_PARAM",
            "BOARDLOG_GATE_PHRASE_PARAM",
            "BOARDLOG_ALLOWED_BOARDS",
            "BOARDLOG_SESSION_TTL_SECONDS",
            "AWS_LAMBDA_FUNCTION_NAME",
        ):
            os.environ.pop(name, None)
        handler._secret_cache.clear()

    def event(self, body=None, headers=None, method="POST"):
        return {
            "requestContext": {"http": {"method": method}},
            "headers": headers or {},
            "body": json.dumps(body or {}),
        }

    def test_options_preflight(self):
        # CORS (incl. preflight) is handled by the Function URL config, not the
        # handler — the handler must not emit Access-Control-* headers.
        response = handler.lambda_handler(self.event(method="OPTIONS"), None)
        self.assertEqual(response["statusCode"], 204)
        self.assertNotIn("Access-Control-Allow-Origin", response["headers"])

    def test_requires_access_key_when_configured(self):
        os.environ["BOARDLOG_ACCESS_KEY"] = "secret"
        response = handler.lambda_handler(self.event({"username": "u", "password": "p"}), None)
        self.assertEqual(response["statusCode"], 403)

    @unittest.mock.patch("backend.boardlog_lambda.handler.export_logbook")
    def test_exports_json_rows(self, mock_export):
        os.environ["BOARDLOG_ACCESS_KEY"] = "secret"
        mock_export.return_value = [{"board": "tension", "climb_name": "Test"}]

        response = handler.lambda_handler(
            self.event(
                {"board": "tension", "username": "u", "password": "p"},
                headers={"X-Board-Room-Key": "secret"},
            ),
            None,
        )

        self.assertEqual(response["statusCode"], 200)
        payload = json.loads(response["body"])
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(payload["rows"][0]["climb_name"], "Test")
        mock_export.assert_called_once_with("tension", "u", "p")

    def test_rejects_unknown_board(self):
        response = handler.lambda_handler(
            self.event({"board": "kilter", "username": "u", "password": "p"}),
            None,
        )
        self.assertEqual(response["statusCode"], 400)

    def test_unlock_accepts_correct_gate_phrase(self):
        os.environ["BOARDLOG_GATE_PHRASE"] = "open sesame"
        response = handler.lambda_handler(
            self.event({"action": "unlock"}, headers={"X-Board-Gate": "open sesame"}),
            None,
        )
        self.assertEqual(response["statusCode"], 200)
        self.assertTrue(json.loads(response["body"])["ok"])

    def test_unlock_rejects_wrong_gate_phrase(self):
        os.environ["BOARDLOG_GATE_PHRASE"] = "open sesame"
        response = handler.lambda_handler(
            self.event({"action": "unlock"}, headers={"X-Board-Gate": "nope"}),
            None,
        )
        self.assertEqual(response["statusCode"], 403)

    def test_export_blocked_by_wrong_gate_even_with_access_key(self):
        os.environ["BOARDLOG_GATE_PHRASE"] = "open sesame"
        os.environ["BOARDLOG_ACCESS_KEY"] = "secret"
        response = handler.lambda_handler(
            self.event(
                {"board": "tension", "username": "u", "password": "p"},
                headers={"X-Board-Room-Key": "secret"},  # gate header missing
            ),
            None,
        )
        self.assertEqual(response["statusCode"], 403)

    @unittest.mock.patch("backend.boardlog_lambda.handler.export_logbook")
    def test_export_requires_both_secrets(self, mock_export):
        os.environ["BOARDLOG_GATE_PHRASE"] = "open sesame"
        os.environ["BOARDLOG_ACCESS_KEY"] = "secret"
        mock_export.return_value = []

        response = handler.lambda_handler(
            self.event(
                {"board": "tension", "username": "u", "password": "p"},
                headers={"X-Board-Gate": "open sesame", "X-Board-Room-Key": "secret"},
            ),
            None,
        )
        self.assertEqual(response["statusCode"], 200)

    def test_fails_closed_in_lambda_when_secret_unconfigured(self):
        # In Lambda (public Function URL) a missing secret must refuse requests
        # rather than silently disabling auth.
        os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "boardlog"
        response = handler.lambda_handler(
            self.event({"board": "tension", "username": "u", "password": "p"}),
            None,
        )
        self.assertEqual(response["statusCode"], 403)

    def test_decodes_base64_body(self):
        import base64

        os.environ["BOARDLOG_ACCESS_KEY"] = "secret"
        raw = json.dumps({"action": "unlock"})
        event = {
            "requestContext": {"http": {"method": "POST"}},
            "headers": {},
            "body": base64.b64encode(raw.encode("utf-8")).decode("ascii"),
            "isBase64Encoded": True,
        }
        response = handler.lambda_handler(event, None)
        # Gate unconfigured locally -> unlock succeeds; the point is the body parsed.
        self.assertEqual(response["statusCode"], 200)

    @unittest.mock.patch("backend.boardlog_lambda.handler.export_logbook")
    def test_bad_board_credentials_return_401(self, mock_export):
        mock_export.side_effect = handler.BoardLoginError("Board login failed; check your username and password")
        response = handler.lambda_handler(
            self.event({"board": "tension", "username": "u", "password": "wrong"}),
            None,
        )
        self.assertEqual(response["statusCode"], 401)
        self.assertIn("login failed", json.loads(response["body"])["error"].lower())

    @unittest.mock.patch("backend.boardlog_lambda.handler.export_logbook")
    def test_internal_errors_are_not_leaked(self, mock_export):
        mock_export.side_effect = ValueError("internal detail: /etc/secret/path")
        response = handler.lambda_handler(
            self.event({"board": "tension", "username": "u", "password": "p"}),
            None,
        )
        self.assertEqual(response["statusCode"], 502)
        self.assertNotIn("internal detail", response["body"])

    def test_secrets_are_independent(self):
        # Only the gate is configured; the access key check is disabled and must
        # not block a request that satisfies the gate.
        os.environ["BOARDLOG_GATE_PHRASE"] = "open sesame"
        with unittest.mock.patch(
            "backend.boardlog_lambda.handler.export_logbook", return_value=[]
        ):
            response = handler.lambda_handler(
                self.event(
                    {"board": "tension", "username": "u", "password": "p"},
                    headers={"X-Board-Gate": "open sesame"},
                ),
                None,
            )
        self.assertEqual(response["statusCode"], 200)

    # --- Session tokens: one knock is the page's whole login ---

    def configure_secrets(self):
        os.environ["BOARDLOG_GATE_PHRASE"] = "open sesame"
        os.environ["BOARDLOG_ACCESS_KEY"] = "secret"

    def unlock(self, phrase="open sesame"):
        response = handler.lambda_handler(
            self.event({"action": "unlock"}, headers={"X-Board-Gate": phrase}), None
        )
        return response, json.loads(response["body"])

    def export_with_session(self, token):
        return handler.lambda_handler(
            self.event(
                {"board": "tension", "username": "u", "password": "p"},
                headers={"X-Board-Session": token},
            ),
            None,
        )

    def test_unlock_issues_a_session_token(self):
        self.configure_secrets()
        response, payload = self.unlock()
        self.assertEqual(response["statusCode"], 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(handler.verify_session(payload["session"]))
        # The expiry is readable from the token itself so the page can tell a
        # stale token apart without a round trip.
        self.assertEqual(int(payload["session"].split(".")[0]), payload["expires_at"])
        self.assertAlmostEqual(
            payload["expires_at"], time.time() + handler.DEFAULT_SESSION_TTL_SECONDS, delta=5
        )

    def test_wrong_knock_gets_no_session(self):
        self.configure_secrets()
        response, payload = self.unlock("nope")
        self.assertEqual(response["statusCode"], 403)
        self.assertNotIn("session", payload)

    @unittest.mock.patch("backend.boardlog_lambda.handler.export_logbook")
    def test_export_accepts_a_session_token_alone(self, mock_export):
        self.configure_secrets()
        mock_export.return_value = []
        _, payload = self.unlock()
        response = self.export_with_session(payload["session"])
        self.assertEqual(response["statusCode"], 200)
        mock_export.assert_called_once_with("tension", "u", "p")

    def test_export_rejects_forged_and_expired_sessions(self):
        self.configure_secrets()
        _, payload = self.unlock()
        expiry, signature = payload["session"].split(".")
        flipped = ("0" if signature[0] != "0" else "1") + signature[1:]
        expired = int(time.time()) - 1
        for token in (
            "",
            "garbage",
            f"{expiry}.",
            f"{expiry}.{flipped}",
            # Pushing the expiry out without re-signing must fail.
            f"{int(expiry) + 3600}.{signature}",
            # A correctly signed but already-expired token must fail.
            f"{expired}.{handler.sign_session(expired)}",
        ):
            with self.subTest(token=token):
                self.assertEqual(self.export_with_session(token)["statusCode"], 403)

    def test_rotating_either_secret_revokes_sessions(self):
        self.configure_secrets()
        _, payload = self.unlock()
        token = payload["session"]
        self.assertTrue(handler.verify_session(token))

        os.environ["BOARDLOG_GATE_PHRASE"] = "new knock"
        self.assertFalse(handler.verify_session(token))

        os.environ["BOARDLOG_GATE_PHRASE"] = "open sesame"
        os.environ["BOARDLOG_ACCESS_KEY"] = "rotated"
        self.assertFalse(handler.verify_session(token))

    def test_sessions_fail_closed_in_lambda_without_both_secrets(self):
        os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "boardlog"
        os.environ["BOARDLOG_GATE_PHRASE"] = "open sesame"  # access key missing
        response, payload = self.unlock()
        # The gate itself still verifies, but no session can be minted...
        self.assertEqual(response["statusCode"], 200)
        self.assertNotIn("session", payload)
        # ...and nothing verifies either.
        self.assertFalse(handler.verify_session(f"{int(time.time()) + 60}.abc"))

    def test_malformed_session_headers_are_refused_not_crashed(self):
        # Anything that is not "<ascii digits>.<signature>" must be a clean 403,
        # never a 502 from int() choking on Unicode digits or a huge number.
        self.configure_secrets()
        for token in ("\u00b2.x", "\u0663\u0664.x", "9" * 5000 + ".x", "1e9.x", ".", "..", "-5.x"):
            with self.subTest(token=token):
                self.assertEqual(self.export_with_session(token)["statusCode"], 403)

    def test_sessions_need_both_secrets_even_outside_lambda(self):
        # A half-configured local run must not mint a token that bypasses the
        # one secret that IS configured; the header path still enforces it.
        os.environ["BOARDLOG_ACCESS_KEY"] = "secret"  # gate phrase unset
        response, payload = self.unlock()
        self.assertEqual(response["statusCode"], 200)
        self.assertNotIn("session", payload)
        self.assertFalse(handler.verify_session(f"{int(time.time()) + 60}.abc"))
        response = handler.lambda_handler(
            self.event({"board": "tension", "username": "u", "password": "p"}), None
        )
        self.assertEqual(response["statusCode"], 403)

    def test_session_ttl_is_configurable(self):
        self.configure_secrets()
        os.environ["BOARDLOG_SESSION_TTL_SECONDS"] = "60"
        _, payload = self.unlock()
        self.assertAlmostEqual(payload["expires_at"], time.time() + 60, delta=5)

        os.environ["BOARDLOG_SESSION_TTL_SECONDS"] = "not-a-number"
        _, payload = self.unlock()
        self.assertAlmostEqual(
            payload["expires_at"], time.time() + handler.DEFAULT_SESSION_TTL_SECONDS, delta=5
        )

    def test_export_log_line_names_the_auth_mechanism_but_not_the_token(self):
        self.configure_secrets()
        _, payload = self.unlock()
        with unittest.mock.patch("backend.boardlog_lambda.handler.export_logbook", return_value=[]):
            with unittest.mock.patch("builtins.print") as mock_print:
                self.export_with_session(payload["session"])
        printed = [str(call.args[0]) for call in mock_print.call_args_list if call.args]
        logged = [json.loads(line) for line in printed if line.startswith("{")]
        self.assertEqual(logged[-1]["auth"], "session")
        self.assertNotIn(payload["session"], "\n".join(printed))


if __name__ == "__main__":
    unittest.main()
