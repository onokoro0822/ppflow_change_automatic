from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from commercial_profiles import build_commercial_profile
from trip_chain_quality import apply_analysis_quality
from trip_chains import build_trip_chain_database


def write_rows(path: Path, rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        csv.writer(output).writerows(rows)


class CommercialProfileTest(unittest.TestCase):
    def make_database(self, directory: Path) -> Path:
        source = directory / "trip_23101.csv"
        database = directory / "chains.sqlite3"
        write_rows(
            source,
            [
                ["person-a", 28_800, 136.90, 35.10, 136.91, 35.11, 4, 2, 21],
                ["person-a", 43_200, 136.91, 35.11, 136.911, 35.111, 1, 100, 21],
                ["person-a", 46_800, 136.911, 35.111, 136.91, 35.11, 1, 1, 21],
                ["person-b", 54_000, 137.20, 35.50, 137.21, 35.51, 3, 200, 23],
                ["person-c", 64_800, 136.912, 35.112, 136.913, 35.113, 2, 400, 15],
            ],
        )
        build_trip_chain_database([source], database)
        apply_analysis_quality(database)
        return database

    def test_builds_all_city_profile_with_chain_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            database = self.make_database(directory)
            output_dir = directory / "profile"

            profile = build_commercial_profile(database, output_dir)

            self.assertEqual(profile["scope"], {"type": "all_city"})
            self.assertEqual(profile["counts"]["selected_trips"], 3)
            self.assertEqual(profile["counts"]["unique_persons"], 3)
            self.assertEqual(profile["missing_counts"]["employment_status"], 0)
            previous = {
                row["category"]: row["count"]
                for row in profile["distributions"]["previous_trip_purpose"]
            }
            self.assertEqual(previous["2:通勤"], 1)
            self.assertEqual(previous["なし（チェーン先頭）"], 2)
            previous_transport = {
                row["category"]: row["count"]
                for row in profile["distributions"]["previous_transport_mode"]
            }
            self.assertEqual(previous_transport["4:電車"], 1)
            transport_sequences = {
                row["category"]: row["count"]
                for row in profile["distributions"]["transport_sequence"]
            }
            self.assertEqual(
                transport_sequences[
                    "4:電車 → 1:徒歩 → 1:徒歩"
                ],
                1,
            )
            self.assertTrue((output_dir / "profile.json").exists())
            self.assertTrue((output_dir / "profile_counts.csv").exists())
            self.assertTrue((output_dir / "selected_trip_sample.csv").exists())

    def test_destination_radius_scope_filters_by_exact_distance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            database = self.make_database(directory)
            output_dir = directory / "nearby"

            profile = build_commercial_profile(
                database,
                output_dir,
                target_lon=136.911,
                target_lat=35.111,
                radius_km=0.5,
            )

            self.assertEqual(profile["scope"]["type"], "destination_radius")
            self.assertEqual(profile["counts"]["selected_trips"], 2)
            self.assertEqual(profile["counts"]["unique_persons"], 2)
            purposes = {
                row["category"]: row["count"]
                for row in profile["distributions"]["trip_purpose"]
            }
            self.assertEqual(purposes, {"100:買い物": 1, "400:自由行動": 1})

    def test_requires_complete_target_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            database = self.make_database(directory)
            with self.assertRaises(ValueError):
                build_commercial_profile(database, directory / "bad", target_lon=136.9)

    def test_quality_filter_excludes_invalid_time_and_splits_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "trip_23101.csv"
            database = directory / "chains.sqlite3"
            write_rows(
                source,
                [
                    ["person-a", 100, 136.90, 35.10, 136.91, 35.11, 1, 2, 21],
                    ["person-a", 200, 137.20, 35.50, 137.21, 35.51, 1, 100, 21],
                    ["person-a", 90_000, 137.21, 35.51, 137.22, 35.52, 1, 400, 21],
                ],
            )
            build_trip_chain_database([source], database)
            apply_analysis_quality(database)

            profile = build_commercial_profile(database, directory / "profile")

            self.assertEqual(profile["counts"]["commercial_trips_in_database"], 2)
            self.assertEqual(profile["counts"]["selected_trips"], 1)
            self.assertEqual(
                profile["counts"]["excluded_invalid_time_commercial_trips"], 1
            )
            self.assertEqual(profile["chain_endpoint_counts"]["without_previous_trip"], 1)

    def test_polygon_boundary_scope_with_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            database = self.make_database(directory)
            boundary = directory / "boundary.geojson"
            boundary.write_text(
                json.dumps(
                    {
                        "type": "Feature",
                        "properties": {"name": "test facility"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [136.9105, 35.1105],
                                    [136.9115, 35.1105],
                                    [136.9115, 35.1115],
                                    [136.9105, 35.1115],
                                    [136.9105, 35.1105],
                                ]
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            exact = build_commercial_profile(
                database,
                directory / "exact",
                boundary_geojson=boundary,
            )
            buffered = build_commercial_profile(
                database,
                directory / "buffered",
                boundary_geojson=boundary,
                boundary_buffer_m=250.0,
            )

            self.assertEqual(exact["scope"]["type"], "destination_polygon_buffer")
            self.assertEqual(exact["scope"]["facility_name"], "test facility")
            self.assertEqual(exact["counts"]["selected_trips"], 1)
            self.assertEqual(buffered["counts"]["selected_trips"], 2)

    def test_rejects_combined_polygon_and_radius_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            database = self.make_database(directory)
            boundary = directory / "boundary.geojson"
            boundary.write_text(
                '{"type":"Polygon","coordinates":[[[136.9,35.1],'
                '[136.91,35.1],[136.91,35.11],[136.9,35.1]]]}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                build_commercial_profile(
                    database,
                    directory / "bad",
                    target_lon=136.9,
                    target_lat=35.1,
                    radius_km=1.0,
                    boundary_geojson=boundary,
                )


if __name__ == "__main__":
    unittest.main()
