import json
import os
import unittest
import unittest.mock

import backend.boardlog_lambda.handler as handler


class TestBoardLogLambda(unittest.TestCase):
    def setUp(self):
        os.environ.pop("BOARDLOG_ACCESS_KEY", None)
        os.environ.pop("BOARDLOG_ALLOWED_BOARDS", None)

    def event(self, body=None, headers=None, method="POST"):
        return {
            "requestContext": {"http": {"method": method}},
            "headers": headers or {},
            "body": json.dumps(body or {}),
        }

    def test_options_preflight(self):
        response = handler.lambda_handler(self.event(method="OPTIONS"), None)
        self.assertEqual(response["statusCode"], 204)
        self.assertEqual(response["headers"]["Access-Control-Allow-Methods"], "OPTIONS,POST")

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


if __name__ == "__main__":
    unittest.main()
