from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from trip_chains import build_trip_chain_database


def write_rows(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        csv.writer(output).writerows(rows)


class TripChainTest(unittest.TestCase):
    def test_reconstructs_people_across_files_and_links_adjacent_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            first = directory / "trip_23101.csv"
            second = directory / "trip_23102.csv"
            database = directory / "chains.sqlite3"
            summary_path = directory / "summary.json"
            sample_path = directory / "sample.csv"

            write_rows(
                first,
                [
                    ["person-a", 500, 136.90, 35.10, 136.91, 35.11, 1, 1, 21],
                    ["person-b", 300, 136.80, 35.00, 136.81, 35.01, 2, 100, 22],
                    ["person-c", 100, 136.70, 35.00, 136.71, 35.01, 1, 2, 21],
                ],
            )
            write_rows(
                second,
                [
                    ["person-a", 100, 136.89, 35.09, 136.90, 35.10, 4, 2, 21],
                    ["person-c", 200, 137.20, 35.50, 137.21, 35.51, 1, 400, 21],
                ],
            )

            summary = build_trip_chain_database(
                [first, second],
                database,
                summary_path=summary_path,
                sample_csv_path=sample_path,
                continuity_tolerance_m=50.0,
                batch_size=2,
            )

            self.assertEqual(summary["input_files"], 2)
            self.assertEqual(summary["input_trips"], 5)
            self.assertEqual(summary["persons"], 3)
            self.assertEqual(summary["multi_trip_persons"], 2)
            self.assertEqual(summary["persons_spanning_multiple_files"], 2)
            self.assertEqual(summary["adjacent_pairs"], 2)
            self.assertEqual(summary["continuous_pairs"], 1)
            self.assertEqual(summary["discontinuous_pairs"], 1)
            self.assertEqual(summary["max_trips_per_person"], 2)
            self.assertTrue(summary_path.exists())
            self.assertEqual(len(sample_path.read_text(encoding="utf-8").splitlines()), 6)

            connection = sqlite3.connect(database)
            rows = connection.execute(
                """
                SELECT sequence_index, departure_time_sec,
                       previous_trip_purpose, next_trip_purpose,
                       spatial_continuity_valid
                FROM trip_chain_rows
                WHERE person_id = 'person-a'
                ORDER BY sequence_index
                """
            ).fetchall()
            connection.close()

            self.assertEqual(rows[0], (1, 100, None, "1", None))
            self.assertEqual(rows[1], (2, 500, "2", None, 1))

    def test_rejects_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "trip_23101.csv"
            database = directory / "chains.sqlite3"
            write_rows(
                source,
                [["person-a", 100, 136.89, 35.09, 136.90, 35.10, 1, 100, 21]],
            )
            database.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                build_trip_chain_database([source], database)
            self.assertEqual(database.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
