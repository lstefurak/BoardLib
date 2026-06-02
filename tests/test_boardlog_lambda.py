import json
import os
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


if __name__ == "__main__":
    unittest.main()
