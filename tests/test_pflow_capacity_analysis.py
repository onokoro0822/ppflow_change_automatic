import csv
import json
import tempfile
import unittest
from pathlib import Path

import pflow_capacity_analysis as analysis


class PflowCapacityAnalysisTest(unittest.TestCase):
    def test_target_coordinates_are_in_expected_mesh(self):
        self.assertEqual(
            analysis.third_mesh_code(analysis.TARGET_LON, analysis.TARGET_LAT),
            analysis.TARGET_MESH,
        )

    def test_summarizes_target_facility_mesh_and_previous_admin(self):
        with tempfile.TemporaryDirectory() as temp:
            scenario = Path(temp) / "baseline" / "23"
            scenario.mkdir(parents=True)
            rows = [
                [1, 40, 1, 1, 0, 3600, 0, 136.90, 35.20, "23201"],
                [1, 40, 1, 1, 3600, 3600, 100, analysis.TARGET_LON, analysis.TARGET_LAT, "23105"],
                [2, 30, 2, 2, 0, 7200, 0, 136.80, 35.10, "23106"],
                [2, 30, 2, 2, 7200, 3600, 100, 136.8845, 35.1697, "23105"],
            ]
            with (scenario / "sample.csv").open("w", newline="") as handle:
                csv.writer(handle).writerows(rows)
            result = analysis.summarize_scenario(scenario.parent)
            self.assertEqual(result["target_facility"], 1)
            self.assertEqual(result["target_mesh"], 2)
            self.assertEqual(result["target_mesh_other_facilities"], 1)
            self.assertEqual(result["target_mesh_inflow_by_previous_gcode"], {"23106": 1, "23201": 1})

    def test_ten_scenarios_are_complete(self):
        scenario_dir = Path(__file__).parents[1] / "config" / "pflow_capacity"
        paths = sorted(scenario_dir.glob("*.json"))
        self.assertEqual(len(paths), 10)
        ids = set()
        for path in paths:
            data = json.loads(path.read_text())
            ids.add(data["scenarioId"])
            self.assertEqual(data["target"]["meshCode"], analysis.TARGET_MESH)
            self.assertGreater(data["meshCapacityMultiplier"], 0)
            if data["injectFacility"]:
                self.assertGreater(data["facility"]["capacity"], 0)
        self.assertIn("baseline", ids)
        self.assertEqual(len(ids), 10)


if __name__ == "__main__":
    unittest.main()
