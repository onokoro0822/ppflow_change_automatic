import tempfile
import unittest
from pathlib import Path

import pflow_diversion_analysis as diversion
from pflow_combined_origin_adjustment import FACILITY_LAT_TEXT, FACILITY_LON_TEXT


def write(root: Path, rows):
    (root / "23").mkdir(parents=True)
    (root / "23" / "p.csv").write_text("".join(",".join(r) + "\n" for r in rows), encoding="utf-8")


class DiversionTest(unittest.TestCase):
    def test_counts_moves_by_destination_and_source_store(self):
        home = ["1", "30", "1", "1", "0", "100", "1", "136.900000", "35.100000", "23110"]
        shop = ["1", "30", "1", "1", "100", "100", "100", "136.881000", "35.171000", "23104"]
        moved = shop[:7] + [FACILITY_LON_TEXT, FACILITY_LAT_TEXT, "23105"]
        names = {("136.881000", "35.171000"): ("テスト百貨店", "23105")}
        with tempfile.TemporaryDirectory() as tmp:
            base, scenario = Path(tmp) / "base", Path(tmp) / "scenario"
            write(base, [home, shop])
            write(scenario, [home, moved])
            result = diversion.diversions(base / "23", scenario / "23", names)
        self.assertEqual(result["changed_shopping_rows_by_destination"], {"new_facility": 1})
        # The store's own municipality wins over the gcode Pseudo-PFLOW chose.
        self.assertEqual(result["new_facility_visitors_previous_district"], {"名古屋市中村区": 1})
        self.assertEqual(result["new_facility_visitors_previous_store"][0]["store"], "テスト百貨店")
        self.assertEqual(result["new_facility_visitors_previous_distance"]["0〜500m"], 1)

    def test_distance_band(self):
        self.assertEqual(diversion.distance_band(100), "0〜500m")
        self.assertEqual(diversion.distance_band(2500), "1〜3km")
        self.assertEqual(diversion.distance_band(25000), "10km以上")


if __name__ == "__main__":
    unittest.main()
