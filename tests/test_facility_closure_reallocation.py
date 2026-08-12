from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from facility_closure_reallocation import (
    build_closure_reallocation,
    deterministic_person_sample,
)
from trip_chain_quality import apply_analysis_quality
from trip_chains import build_trip_chain_database


def write_rows(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        csv.writer(output).writerows(rows)


class FacilityClosureReallocationTest(unittest.TestCase):
    def test_person_sample_is_reproducible(self) -> None:
        rows = [{"person_id": value} for value in ("a", "b", "c")]
        self.assertEqual(
            deterministic_person_sample(rows, 2, 42),
            deterministic_person_sample(rows, 2, 42),
        )

    def test_reallocates_shopping_trip_and_exports_mobmap(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "trips.csv"
            database = directory / "chains.sqlite3"
            write_rows(
                source,
                [
                    ["a", 100, 136.80, 35.00, 136.90, 35.10, 1, 100, 21],
                    ["a", 500, 136.90, 35.10, 136.82, 35.02, 1, 1, 21],
                    ["a", 700, 136.82, 35.02, 136.90, 35.10, 1, 100, 21],
                    ["a", 900, 136.90, 35.10, 136.84, 35.04, 1, 1, 21],
                    ["b", 200, 136.81, 35.01, 136.95, 35.15, 3, 100, 23],
                    ["b", 600, 136.95, 35.15, 136.83, 35.03, 3, 1, 23],
                    ["c", 300, 136.82, 35.02, 136.95, 35.15, 3, 100, 21],
                    ["d", 400, 136.83, 35.03, 136.96, 35.16, 3, 100, 21],
                ],
            )
            build_trip_chain_database([source], database)
            apply_analysis_quality(database)
            scenario = directory / "scenario.json"
            scenario.write_text(
                json.dumps(
                    {
                        "scenario_id": "test",
                        "source_facility": {
                            "longitude": 136.90,
                            "latitude": 35.10,
                            "radius_m": 100,
                            "eligible_trip_purpose_codes": ["100"],
                        },
                        "changed_person_count": None,
                        "random_seed": 42,
                        "alternative_destinations": {
                            "coordinate_rounding_decimals": 5,
                            "minimum_observed_shopping_trips": 1,
                            "attractiveness_exponent": 1.0,
                            "distance_decay_exponent": 1.5,
                            "minimum_distance_m": 100,
                            "selection_method": "test empirical choice",
                        },
                        "mobmap": {"base_date": "2000-01-01"},
                        "limitations": [],
                    }
                ),
                encoding="utf-8",
            )
            output = directory / "output"
            summary = build_closure_reallocation(database, scenario, output)

            self.assertEqual(summary["source_extraction"]["affected_shopping_trips"], 2)
            self.assertEqual(summary["source_extraction"]["selected_people"], 1)
            self.assertEqual(summary["source_extraction"]["selected_shopping_trips"], 2)
            self.assertEqual(
                summary["source_extraction"]["selection_mode"],
                "all_affected_people",
            )
            self.assertEqual(summary["changes"]["incoming_destinations_changed"], 2)
            self.assertEqual(summary["changes"]["outgoing_origins_changed"], 2)
            self.assertEqual(
                summary["chain_validation"]["post_change_continuity_violations"], 0
            )
            with (output / "selected_visits.csv").open(
                encoding="utf-8", newline=""
            ) as input_file:
                selected = list(csv.DictReader(input_file))
            self.assertNotEqual(
                selected[0]["original_destination_lon"],
                selected[0]["new_destination_lon"],
            )
            with (output / "selected_people.csv").open(
                encoding="utf-8", newline=""
            ) as input_file:
                people = list(csv.DictReader(input_file))
            self.assertEqual(len(people), 1)
            self.assertEqual(people[0]["affected_visit_count"], "2")
            for filename in ("mobmap_before.csv", "mobmap_after.csv"):
                with (output / filename).open(encoding="utf-8", newline="") as input_file:
                    rows = list(csv.DictReader(input_file))
                self.assertEqual(len(rows), 8)
                self.assertEqual(
                    set(("id", "time", "longitude", "latitude"))
                    <= set(rows[0]),
                    True,
                )
                self.assertEqual(
                    [row["time"] for row in rows],
                    sorted(row["time"] for row in rows),
                )
            for filename in (
                "mobmap_changed_trips_before.csv",
                "mobmap_changed_trips_after.csv",
            ):
                with (output / filename).open(encoding="utf-8", newline="") as input_file:
                    focused_rows = list(csv.DictReader(input_file))
                self.assertEqual(len(focused_rows), 8)
                self.assertEqual({row["id"] for row in focused_rows}, {"1", "2"})
                self.assertEqual(
                    [row["time"] for row in focused_rows],
                    sorted(row["time"] for row in focused_rows),
                )
            with (output / "mobmap_changed_trips_before.csv").open(
                encoding="utf-8", newline=""
            ) as input_file:
                focused_before = list(csv.DictReader(input_file))
            with (output / "mobmap_changed_trips_after.csv").open(
                encoding="utf-8", newline=""
            ) as input_file:
                focused_after = list(csv.DictReader(input_file))
            self.assertEqual(sum(int(row["changed"]) for row in focused_before), 0)
            self.assertEqual(sum(int(row["changed"]) for row in focused_after), 4)
            for filename in (
                "mobmap_changed_trips_moving_only_before.csv",
                "mobmap_changed_trips_moving_only_after.csv",
            ):
                with (output / filename).open(encoding="utf-8", newline="") as input_file:
                    moving_rows = list(csv.DictReader(input_file))
                self.assertEqual(len(moving_rows), 8)
                movement_counts: dict[str, int] = {}
                for row in moving_rows:
                    movement_counts[row["id"]] = movement_counts.get(row["id"], 0) + 1
                    self.assertEqual(row["id"], row["trip_id"])
                self.assertEqual(len(movement_counts), 4)
                self.assertEqual(set(movement_counts.values()), {2})
                self.assertEqual(
                    [row["time"] for row in moving_rows],
                    sorted(row["time"] for row in moving_rows),
                )
            self.assertEqual(summary["mobmap"]["moving_only_leg_ids"], 4)
            self.assertEqual(summary["mobmap"]["moving_only_points_per_leg"], 2)


if __name__ == "__main__":
    unittest.main()
