from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from trip_chain_quality import apply_analysis_quality
from trip_chains import build_trip_chain_database


def write_rows(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        csv.writer(output).writerows(rows)


class TripChainQualityTest(unittest.TestCase):
    def test_excludes_invalid_times_and_splits_at_spatial_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "trip_23101.csv"
            database = directory / "chains.sqlite3"
            summary_path = directory / "quality.json"
            write_rows(
                source,
                [
                    ["person-a", -1, 136.90, 35.10, 136.90, 35.10, 1, 1, 21],
                    ["person-a", 100, 136.90, 35.10, 136.91, 35.11, 1, 2, 21],
                    ["person-a", 200, 136.91, 35.11, 136.92, 35.12, 1, 100, 21],
                    ["person-a", 300, 137.20, 35.50, 137.21, 35.51, 1, 400, 21],
                    ["person-a", 86_400, 137.21, 35.51, 137.22, 35.52, 1, 1, 21],
                    ["person-b", 500, 136.80, 35.00, 136.81, 35.01, 1, 200, 23],
                ],
            )
            build_trip_chain_database([source], database)

            summary = apply_analysis_quality(database, summary_path=summary_path)

            self.assertEqual(summary["total_trips"], 6)
            self.assertEqual(summary["time_valid_trips"], 4)
            self.assertEqual(summary["invalid_time_trips"], 2)
            self.assertEqual(summary["analysis_segments"], 3)
            self.assertEqual(summary["retained_adjacent_pairs"], 1)
            self.assertEqual(summary["spatial_gap_breaks"], 1)
            self.assertEqual(summary["previous_invalid_time_breaks"], 1)
            self.assertTrue(summary_path.exists())

            connection = sqlite3.connect(database)
            rows = connection.execute(
                """
                SELECT departure_time_sec, analysis_eligible,
                       segment_index, segment_trip_index,
                       analysis_previous_trip_purpose,
                       analysis_next_trip_purpose, break_before_reason
                FROM analysis_trip_chain_rows
                WHERE person_id = 'person-a'
                ORDER BY sequence_index
                """
            ).fetchall()
            connection.close()

            self.assertEqual(rows[0][1:], (0, None, None, None, None, "invalid_time"))
            self.assertEqual(rows[1][1:], (1, 1, 1, None, "100", "previous_invalid_time"))
            self.assertEqual(rows[2][1:], (1, 1, 2, "2", None, "none"))
            self.assertEqual(rows[3][1:], (1, 2, 1, None, None, "spatial_gap"))
            self.assertEqual(rows[4][1:], (0, None, None, None, None, "invalid_time"))

    def test_rejects_existing_quality_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "trip_23101.csv"
            database = directory / "chains.sqlite3"
            write_rows(
                source,
                [["person-a", 100, 136.90, 35.10, 136.91, 35.11, 1, 100, 21]],
            )
            build_trip_chain_database([source], database)
            apply_analysis_quality(database)
            with self.assertRaises(FileExistsError):
                apply_analysis_quality(database)


if __name__ == "__main__":
    unittest.main()
