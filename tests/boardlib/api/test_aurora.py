import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import requests

import boardlib.api.aurora
from tests.boardlib.api.requests_mocks import get_mock_request, MockResponse


class TestAurora(unittest.TestCase):
    @unittest.mock.patch(
        "requests.post",
        side_effect=get_mock_request(
            json_data={"session": {"token": "test", "user_id": 1234}},
        ),
    )
    def test_login_success(self, mock_post):
        self.assertEqual(
            boardlib.api.aurora.login("aurora", "test", "test"),
            {"token": "test", "user_id": 1234},
        )

    @unittest.mock.patch(
        "requests.post",
        side_effect=get_mock_request(status_code=requests.codes.bad_request),
    )
    def test_login_failure(self, mock_post):
        with self.assertRaises(requests.exceptions.HTTPError):
            boardlib.api.aurora.login("aurora", "test", "test")

    @unittest.mock.patch(
        "requests.get",
        side_effect=get_mock_request(json_data="test_explore"),
    )
    def test_explore(self, mock_get):
        self.assertEqual(boardlib.api.aurora.explore("aurora", "test"), "test_explore")

    @unittest.mock.patch(
        "requests.get",
        side_effect=get_mock_request(status_code=requests.codes.bad_request),
    )
    def test_explore_failure(self, mock_get):
        with self.assertRaises(requests.exceptions.HTTPError):
            boardlib.api.aurora.explore("aurora", "test")

    @unittest.mock.patch(
        "requests.post",
        side_effect=get_mock_request(json_data={"ascents": []}),
    )
    def test_get_logbook(self, mock_post):
        self.assertEqual(boardlib.api.aurora.get_ascents("aurora", "test"), [])

    @unittest.mock.patch(
        "requests.post",
        side_effect=get_mock_request(status_code=requests.codes.bad_request),
    )
    def test_get_logbook_failure(self, mock_get):
        with self.assertRaises(requests.exceptions.HTTPError):
            boardlib.api.aurora.get_ascents("aurora", "test")

    @unittest.mock.patch(
        "requests.get",
        side_effect=get_mock_request(json_data="test_get_gyms"),
    )
    def test_get_gyms(self, mock_get):
        self.assertEqual(boardlib.api.aurora.get_gyms("aurora"), "test_get_gyms")

    @unittest.mock.patch(
        "requests.get",
        side_effect=get_mock_request(status_code=requests.codes.bad_request),
    )
    def test_get_gyms_failure(self, mock_get):
        with self.assertRaises(requests.exceptions.HTTPError):
            boardlib.api.aurora.get_gyms("aurora")

    @unittest.mock.patch(
        "requests.get",
        side_effect=get_mock_request(json_data="test_get_user"),
    )
    def test_get_user(self, mock_get):
        self.assertEqual(
            boardlib.api.aurora.get_user("aurora", "test", "test"), "test_get_user"
        )

    @unittest.mock.patch(
        "requests.get",
        side_effect=get_mock_request(status_code=requests.codes.bad_request),
    )
    def test_get_user_failure(self, mock_get):
        with self.assertRaises(requests.exceptions.HTTPError):
            boardlib.api.aurora.get_user("aurora", "test", "test")

    @unittest.mock.patch(
        "requests.get",
        side_effect=get_mock_request(json_data="test_get_climb_stats"),
    )
    def test_get_climb_stats(self, mock_get):
        self.assertEqual(
            boardlib.api.aurora.get_climb_stats("aurora", "test", "test", "test"),
            "test_get_climb_stats",
        )

    @unittest.mock.patch(
        "requests.get",
        side_effect=get_mock_request(status_code=requests.codes.bad_request),
    )
    def test_get_climb_stats_failure(self, mock_get):
        with self.assertRaises(requests.exceptions.HTTPError):
            boardlib.api.aurora.get_climb_stats("aurora", "test", "test", "test")

    @unittest.mock.patch(
        "requests.get",
        side_effect=get_mock_request(text="<h1>test_get_climb_name</h1>"),
    )
    def test_get_climb_name(self, mock_get):
        self.assertEqual(
            boardlib.api.aurora.get_climb_name("aurora", "test"), "test_get_climb_name"
        )

    @unittest.mock.patch(
        "requests.get",
        side_effect=get_mock_request(status_code=requests.codes.bad_request),
    )
    def test_get_climb_name_failure(self, mock_get):
        with self.assertRaises(requests.exceptions.HTTPError):
            boardlib.api.aurora.get_climb_name("aurora", "test")

    @unittest.mock.patch(
        "requests.post",
        side_effect=get_mock_request(json_data="test_sync"),
    )
    def test_sync(self, mock_get):
        self.assertEqual(
            boardlib.api.aurora.user_sync("aurora", "test", "test"), "test_sync"
        )

    @unittest.mock.patch(
        "requests.post",
        side_effect=get_mock_request(status_code=requests.codes.bad_request),
    )
    def test_sync_failure(self, mock_get):
        with self.assertRaises(requests.exceptions.HTTPError):
            boardlib.api.aurora.user_sync("aurora", "test", "test")

    @unittest.mock.patch(
        "boardlib.api.aurora.get_gyms",
        side_effect=lambda *args, **kwargs: {
            "gyms": [
                {
                    "id": 1575,
                    "username": "testuser",
                    "name": "testgym",
                    "latitude": 51.43236,
                    "longitude": 6.7432,
                }
            ]
        },
    )
    def test_gym_boards(self, mock_get_gyms):
        self.assertEqual(
            next(boardlib.api.aurora.gym_boards("aurora")),
            {
                "name": "testgym",
                "latitude": 51.43236,
                "longitude": 6.7432,
            },
        )


class TestLogbookEntries(unittest.TestCase):
    """logbook_entries against a temp shared database and mocked API calls."""

    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.db_path = Path(self._tempdir.name) / "test.db"
        connection = sqlite3.connect(self.db_path)
        with connection:
            connection.execute("CREATE TABLE climbs(uuid TEXT, name TEXT)")
            connection.execute(
                "CREATE TABLE difficulty_grades(difficulty INTEGER, boulder_name TEXT)"
            )
            connection.execute(
                "CREATE TABLE climb_stats("
                "climb_uuid TEXT, angle INTEGER, display_difficulty FLOAT, "
                "benchmark_difficulty FLOAT, ascensionist_count INTEGER, "
                "difficulty_average FLOAT, quality_average FLOAT, "
                "fa_username TEXT, fa_at TEXT)"
            )
            connection.executemany(
                "INSERT INTO climbs VALUES (?, ?)",
                [("c1", "Test Climb"), ("c2", "Project Climb")],
            )
            connection.executemany(
                "INSERT INTO difficulty_grades VALUES (?, ?)",
                [(15, "6c/V5"), (16, "7a/V6")],
            )
            connection.executemany(
                "INSERT INTO climb_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("c1", 40, 15.0, 15.0, 321, 14.8, 2.7, "setter", "2020-01-01"),
                    ("c2", 40, 16.0, None, 12, 16.0, 1.9, "setter", "2020-01-01"),
                ],
            )
        connection.close()

    @unittest.mock.patch("boardlib.api.aurora.get_attempts")
    @unittest.mock.patch("boardlib.api.aurora.get_ascents")
    def test_logbook_includes_community_stats(self, mock_ascents, mock_attempts):
        mock_ascents.return_value = [
            {
                "climb_uuid": "c1",
                "angle": 40,
                "is_listed": True,
                "is_mirror": False,
                "difficulty": 15,
                "attempt_id": 0,
                "bid_count": 2,
                "comment": "sent it",
                "climbed_at": "2026-01-02 10:00:00",
                "user_id": 1,
            }
        ]
        mock_attempts.return_value = [
            {
                "climb_uuid": "c2",
                "angle": 40,
                "is_mirror": False,
                "bid_count": 3,
                "comment": "",
                "climbed_at": "2026-01-01 09:00:00",
                "created_at": "2026-01-01 09:00:00",
                "user_id": 1,
            }
        ]

        frame = boardlib.api.aurora.logbook_entries("tension", "token", self.db_path)

        for column in ("climb_uuid", "ascensionist_count", "quality_average"):
            self.assertIn(column, frame.columns)

        ascent = frame[frame["climb_name"] == "Test Climb"].iloc[0]
        self.assertTrue(ascent["is_ascent"])
        self.assertEqual(ascent["ascensionist_count"], 321)
        self.assertEqual(ascent["quality_average"], 2.7)
        self.assertEqual(ascent["climb_uuid"], "c1")
        self.assertTrue(ascent["is_benchmark"])

        attempt = frame[frame["climb_name"] == "Project Climb"].iloc[0]
        self.assertFalse(attempt["is_ascent"])
        self.assertEqual(attempt["ascensionist_count"], 12)
        self.assertEqual(attempt["quality_average"], 1.9)
        self.assertEqual(attempt["displayed_grade"], "7a/V6")
        self.assertFalse(attempt["is_benchmark"])


if __name__ == "__main__":
    unittest.main()
