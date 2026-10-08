import tempfile
import unittest
from pathlib import Path

import pflow_scenario_effects as effects
from pflow_combined_origin_adjustment import FACILITY_LAT_TEXT, FACILITY_LON_TEXT


def write(root: Path, rows):
    (root / "23").mkdir(parents=True)
    (root / "23" / "p.csv").write_text("".join(",".join(r) + "\n" for r in rows), encoding="utf-8")


class OccupancyTest(unittest.TestCase):
    def test_fractional_hours(self):
        occupancy = effects.hourly_occupancy([(10 * 3600 + 1800, 12 * 3600)])
        self.assertEqual(occupancy, {10: 0.5, 11: 1.0})

    def test_change_band(self):
        self.assertEqual(effects.change_band(-6), "5km以上短く")
        self.assertEqual(effects.change_band(0.3), "±1km以内")
        self.assertEqual(effects.change_band(7), "5km以上長く")


class ScenarioEffectsTest(unittest.TestCase):
    def test_moved_visitor_day_and_stay(self):
        home = ["1", "30", "1", "1", "0", "36000", "1", "136.883980", "35.200000", "23105"]
        shop_before = ["1", "30", "1", "1", "36000", "7200", "100", "136.883980", "35.190000", "23105"]
        shop_after = shop_before[:7] + [FACILITY_LON_TEXT, FACILITY_LAT_TEXT, "23105"]
        back = ["1", "30", "1", "1", "43200", "43200", "1", "136.883980", "35.200000", "23105"]
        with tempfile.TemporaryDirectory() as tmp:
            base, scenario = Path(tmp) / "base", Path(tmp) / "scenario"
            write(base, [home, shop_before, back])
            write(scenario, [home, shop_after, back])
            result = effects.scenario_effects(base / "23", scenario / "23")
        self.assertEqual(result["moved_people"], 1)
        self.assertGreater(result["mean_day_km"]["after"], result["mean_day_km"]["before"])
        # The ~3.4 km trip home at 20 km/h (~10 min) comes out of the 2-hour stay.
        self.assertLess(result["stay_minutes"]["median"], 120)
        self.assertGreater(result["stay_minutes"]["median"], 105)


if __name__ == "__main__":
    unittest.main()
