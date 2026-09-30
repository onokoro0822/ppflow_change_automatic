import json
import tempfile
import unittest
from pathlib import Path

import pflow_capacity_calibration as calibration


SWEEP_ROWS = [
    {"scenario_id": "baseline", "shopping_total": 38487.0, "target_facility": 0.0,
     "target_mesh": 262.0, "target_mesh_other_facilities": 262.0,
     "target_admin_shopping": 896.0},
    {"scenario_id": "facility_10x", "shopping_total": 38487.0, "target_facility": 6.0,
     "target_mesh": 262.0, "target_mesh_other_facilities": 256.0,
     "target_admin_shopping": 896.0},
    {"scenario_id": "facility_100x", "shopping_total": 38487.0, "target_facility": 55.0,
     "target_mesh": 262.0, "target_mesh_other_facilities": 207.0,
     "target_admin_shopping": 896.0},
    {"scenario_id": "mesh_2x", "shopping_total": 38487.0, "target_facility": 0.0,
     "target_mesh": 377.0, "target_mesh_other_facilities": 377.0,
     "target_admin_shopping": 896.0},
    {"scenario_id": "mesh_5x", "shopping_total": 38487.0, "target_facility": 0.0,
     "target_mesh": 556.0, "target_mesh_other_facilities": 556.0,
     "target_admin_shopping": 896.0},
    {"scenario_id": "mesh_10x", "shopping_total": 38487.0, "target_facility": 0.0,
     "target_mesh": 661.0, "target_mesh_other_facilities": 661.0,
     "target_admin_shopping": 896.0},
    {"scenario_id": "combined_mesh_2x_facility_10x", "shopping_total": 38487.0,
     "target_facility": 9.0, "target_mesh": 377.0,
     "target_mesh_other_facilities": 368.0, "target_admin_shopping": 896.0},
    {"scenario_id": "combined_mesh_5x_facility_10x", "shopping_total": 38487.0,
     "target_facility": 13.0, "target_mesh": 556.0,
     "target_mesh_other_facilities": 543.0, "target_admin_shopping": 896.0},
    {"scenario_id": "combined_mesh_10x_facility_10x", "shopping_total": 38487.0,
     "target_facility": 14.0, "target_mesh": 661.0,
     "target_mesh_other_facilities": 647.0, "target_admin_shopping": 896.0},
]


class PflowCapacityCalibrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = calibration.load_capacity_scenarios(
            Path(__file__).parents[1] / "config" / "pflow_capacity"
        )

    def test_reads_bridge_and_distribution_demands(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = Path(temp) / "bridge.json"
            bridge.write_text(json.dumps({"target_arrival_trip_count": 1234}))
            self.assertEqual(calibration.load_target_arrivals(bridge)[0], 1234)

            distribution = Path(temp) / "distribution.json"
            distribution.write_text(json.dumps({
                "scenarios": [{
                    "id": "all_commercial",
                    "age_gender_distribution": {
                        "facilities": [{"key": "commercial", "estimated_arrivals": 25284}]
                    },
                }]
            }))
            self.assertEqual(calibration.load_target_arrivals(distribution)[0], 25284)

    def test_calibrates_one_scenario_close_to_target(self):
        scenario, report = calibration.calibrate_capacity(
            SWEEP_ROWS, self.scenarios, 25284, 50, 0.70
        )
        self.assertGreater(scenario["meshCapacityMultiplier"], 10)
        self.assertGreater(scenario["facility"]["capacity"], 1_000_000)
        self.assertEqual(set(scenario), {
            "scenarioId", "transition", "target", "meshCapacityMultiplier",
            "injectFacility", "facility",
        })
        prediction = report["prediction_after_rounding"]["scaled_full_population_arrivals"]
        self.assertLess(abs(prediction - 25284), 100)
        self.assertGreater(report["mesh_model"]["r_squared"], 0.99)

    def test_rejects_target_above_admin_choice_ceiling(self):
        with self.assertRaisesRegex(ValueError, "not reachable"):
            calibration.calibrate_capacity(
                SWEEP_ROWS, self.scenarios, 50_000, 50, 0.70
            )

    def test_builds_scaled_evaluation(self):
        report = {"target": {"full_population_arrival_trips": 25284}}
        result = calibration.build_evaluation(report, 500, 50)
        self.assertEqual(result["scaled_full_population_arrivals"], 25000)
        self.assertAlmostEqual(result["absolute_percentage_error"], 284 / 25284 * 100)


if __name__ == "__main__":
    unittest.main()
