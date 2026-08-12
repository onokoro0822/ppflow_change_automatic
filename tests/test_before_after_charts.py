from __future__ import annotations

import unittest

from before_after_charts import aggregate_before_after


class BeforeAfterChartsTest(unittest.TestCase):
    def test_aggregates_people_and_visits_separately(self) -> None:
        summary = {
            "scenario": {
                "source_facility": {
                    "label": "test source",
                    "longitude": 0.0,
                    "latitude": 0.0,
                }
            },
            "distance_km": {
                "incoming_before_mean": 1.0,
                "incoming_after_mean": 2.0,
            },
        }
        visits = [
            {
                "person_id": "a",
                "new_destination_lon": 0.001,
                "new_destination_lat": 0.0,
                "incoming_distance_before_km": 0.2,
                "incoming_distance_after_km": 0.4,
            },
            {
                "person_id": "a",
                "new_destination_lon": 0.01,
                "new_destination_lat": 0.0,
                "incoming_distance_before_km": 1.0,
                "incoming_distance_after_km": 1.2,
            },
            {
                "person_id": "b",
                "new_destination_lon": 0.02,
                "new_destination_lat": 0.0,
                "incoming_distance_before_km": 3.0,
                "incoming_distance_after_km": 3.2,
            },
        ]

        metrics = aggregate_before_after(summary, visits)
        area_200 = next(
            row for row in metrics["area_rows"] if row["radius_m"] == 200
        )

        self.assertEqual(metrics["total_people"], 2)
        self.assertEqual(metrics["total_visits"], 3)
        self.assertEqual(area_200["after_people_inside"], 1)
        self.assertEqual(area_200["after_visits_inside"], 1)
        self.assertEqual(metrics["source_rings"]["50–200m"], 1)
        self.assertEqual(metrics["source_rings"]["1–2km"], 1)
        self.assertEqual(metrics["source_rings"]["2–5km"], 1)


if __name__ == "__main__":
    unittest.main()
