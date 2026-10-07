import unittest

import chukyo_pt_origin_distribution as pt
import pflow_origin_comparison as comparison


ZONE_LABELS = {
    "1": pt.MiddleZoneInfo(("愛知県",), ("名古屋市千種区", "名古屋市東区"), ""),
    "2": pt.MiddleZoneInfo(("愛知県",), ("名古屋市東区",), ""),
    "5": pt.MiddleZoneInfo(("愛知県",), ("名古屋市中村区",), ""),
    "301": pt.MiddleZoneInfo(("三重県",), ("桑名市",), ""),
}


class ComparisonUnitsTest(unittest.TestCase):
    def setUp(self):
        self.units = comparison.ComparisonUnits(ZONE_LABELS)

    def test_zones_sharing_a_municipality_merge_into_one_unit(self):
        self.assertEqual(self.units.unit_for_zone("1"), "名古屋市千種区・名古屋市東区")
        self.assertEqual(self.units.unit_for_zone("2"), "名古屋市千種区・名古屋市東区")
        self.assertEqual(self.units.unit_for_gcode("23101"), "名古屋市千種区・名古屋市東区")
        self.assertEqual(self.units.unit_for_gcode("23105"), "名古屋市中村区")

    def test_outside_aichi_and_unknown_codes(self):
        self.assertEqual(self.units.unit_for_zone("301"), comparison.OUTSIDE_AICHI)
        self.assertEqual(self.units.unit_for_zone("999"), comparison.OUTSIDE_AICHI)
        self.assertEqual(self.units.unit_for_gcode("24205"), comparison.OUTSIDE_AICHI)

    def test_aichi_code_missing_from_pt_table_is_an_error(self):
        with self.assertRaises(ValueError):
            self.units.unit_for_gcode("23201")

    def test_unit_class(self):
        self.assertEqual(comparison.unit_class("名古屋市中村区"), "中村区内")
        self.assertEqual(comparison.unit_class("名古屋市千種区・名古屋市東区"), "名古屋市内（中村区以外）")
        self.assertEqual(comparison.unit_class("清須市"), "名古屋市外の愛知県")


class DistanceTest(unittest.TestCase):
    def test_total_variation_distance(self):
        self.assertAlmostEqual(
            comparison.total_variation_distance({"a": 0.5, "b": 0.5}, {"a": 1.0}), 0.5
        )
        self.assertAlmostEqual(
            comparison.total_variation_distance({"a": 0.2, "b": 0.8}, {"a": 0.2, "b": 0.8}), 0.0
        )

    def test_normalize_excludes_keys(self):
        shares = comparison.normalize({"a": 3, "b": 1, "x": 4}, exclude=["x"])
        self.assertEqual(shares, {"a": 0.75, "b": 0.25})


if __name__ == "__main__":
    unittest.main()
