from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from destination_replacement import (
    build_destination_replacement,
    deterministic_weighted_sample,
)
from trip_chain_quality import apply_analysis_quality
from trip_chains import build_trip_chain_database


def write_rows(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        csv.writer(output).writerows(rows)


class DestinationReplacementTest(unittest.TestCase):
    def test_weighted_sample_is_reproducible_and_unique(self) -> None:
        rows = [
            {"person_id": "a", "preference_score": 1.1},
            {"person_id": "b", "preference_score": 1.2},
            {"person_id": "c", "preference_score": 1.3},
        ]
        first = deterministic_weighted_sample(rows, 2, 42)
        second = deterministic_weighted_sample(rows, 2, 42)
        self.assertEqual(first, second)
        self.assertEqual(len({row["person_id"] for row in first}), 2)

    def test_replaces_destination_and_next_origin_without_changing_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "trip.csv"
            database = directory / "chains.sqlite3"
            write_rows(
                source,
                [
                    ["a", 100, 136.80, 35.00, 136.81, 35.01, 1, 100, 21],
                    ["a", 200, 136.81, 35.01, 136.82, 35.02, 1, 1, 21],
                    ["b", 300, 136.83, 35.03, 136.84, 35.04, 3, 200, 23],
                    ["b", 400, 136.84, 35.04, 136.85, 35.05, 3, 1, 23],
                    ["c", 500, 136.86, 35.06, 136.87, 35.07, 1, 400, 21],
                ],
            )
            build_trip_chain_database([source], database)
            apply_analysis_quality(database)

            import sqlite3

            connection = sqlite3.connect(database)
            trip_ids = dict(
                connection.execute(
                    "SELECT person_id, trip_id FROM trips WHERE trip_purpose IN ('100','200','400')"
                )
            )
            connection.close()
            candidates = directory / "candidates.csv"
            with candidates.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=(
                        "person_id",
                        "trip_id",
                        "preference_score",
                        "trip_purpose",
                        "estimated_visit_probability",
                    ),
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"person_id": "a", "trip_id": trip_ids["a"], "preference_score": 1.2, "trip_purpose": "100:買い物", "estimated_visit_probability": 0.8},
                        {"person_id": "b", "trip_id": trip_ids["b"], "preference_score": 1.3, "trip_purpose": "200:外食", "estimated_visit_probability": 0.8},
                        {"person_id": "c", "trip_id": trip_ids["c"], "preference_score": 1.4, "trip_purpose": "400:自由行動", "estimated_visit_probability": 0.9},
                    ]
                )
            potential_summary = directory / "potential.json"
            potential_summary.write_text(
                json.dumps({"counts": {"estimated_high_preference_visitors": 2}}),
                encoding="utf-8",
            )
            scenario = directory / "scenario.json"
            scenario.write_text(
                json.dumps(
                    {
                        "target": {
                            "longitude": 136.90,
                            "latitude": 35.10,
                            "exclude_existing_destination_radius_m": 10,
                        },
                        "eligible_trip_purpose_codes": ["100", "200"],
                        "target_visitor_count": None,
                        "random_seed": 42,
                    }
                ),
                encoding="utf-8",
            )

            summary = build_destination_replacement(
                database,
                candidates,
                potential_summary,
                scenario,
                directory / "output",
            )

            self.assertEqual(summary["selection"]["selected_visitors"], 2)
            self.assertAlmostEqual(
                summary["selection"]["eligible_candidate_expected_visitors"], 1.6
            )
            self.assertEqual(summary["changes"]["incoming_destinations_changed"], 2)
            self.assertEqual(summary["changes"]["outgoing_origins_changed"], 2)
            self.assertEqual(summary["changes"]["departure_times_changed"], 0)
            self.assertEqual(
                summary["chain_validation"]["post_change_continuity_violations"], 0
            )
            self.assertTrue((directory / "output" / "selected_visits.csv").exists())
            changes_path = directory / "output" / "coordinate_changes.csv"
            with changes_path.open(encoding="utf-8", newline="") as source:
                changes = list(csv.DictReader(source))
            self.assertEqual(len(changes), 4)
            self.assertEqual(
                {row["coordinate_role"] for row in changes}, {"destination", "origin"}
            )
            before_after = (directory / "output" / "before_after_counts.csv").read_text(
                encoding="utf-8"
            )
            self.assertIn("previous_trip_purpose", before_after)
            self.assertIn("next_trip_purpose", before_after)


if __name__ == "__main__":
    unittest.main()
