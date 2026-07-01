import sqlite3
import tempfile
import unittest
from pathlib import Path

import boardlib.db.aurora


CLIMB_STATS_SCHEMA = """
CREATE TABLE climb_stats(
    climb_uuid TEXT,
    angle INTEGER,
    display_difficulty FLOAT,
    benchmark_difficulty FLOAT,
    ascensionist_count INTEGER,
    difficulty_average FLOAT,
    quality_average FLOAT,
    fa_username TEXT,
    fa_at TEXT
)
"""


class TestClimbStatsMapping(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.db_path = Path(self._tempdir.name) / "test.db"
        connection = sqlite3.connect(self.db_path)
        with connection:
            connection.execute(CLIMB_STATS_SCHEMA)
            connection.executemany(
                "INSERT INTO climb_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("c1", 40, 15.0, None, 321, 14.8, 2.7, "setter", "2020-01-01"),
                    ("c1", 45, 16.2, 16.0, 55, 16.2, 3.0, "setter", "2020-01-01"),
                    ("c2", 40, 10.0, None, 5, 10.1, 1.5, "other", "2021-05-05"),
                ],
            )
        connection.close()

    def test_get_climb_stats_mapping(self):
        mapping = boardlib.db.aurora.get_climb_stats_mapping(
            self.db_path, ["c1", "c2", "missing"]
        )
        self.assertEqual(
            mapping[("c1", 45)],
            {
                "display_difficulty": 16.2,
                "benchmark_difficulty": 16.0,
                "ascensionist_count": 55,
                "quality_average": 3.0,
            },
        )
        self.assertEqual(mapping[("c2", 40)]["ascensionist_count"], 5)
        self.assertNotIn(("missing", 40), mapping)

    def test_get_difficulty_stats_mapping_keeps_tuple_shape(self):
        mapping = boardlib.db.aurora.get_difficulty_stats_mapping(
            self.db_path, ["c1"]
        )
        self.assertEqual(mapping[("c1", 40)], (15.0, None))
        self.assertEqual(mapping[("c1", 45)], (16.2, 16.0))


if __name__ == "__main__":
    unittest.main()
