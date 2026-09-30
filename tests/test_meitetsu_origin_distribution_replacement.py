import unittest
from pathlib import Path
from shapely.geometry import Polygon
from meitetsu_origin_distribution_replacement import (
    ZoneBoundary, ZoneClassifier, bounded_allocation,
    deterministic_priority, largest_remainder_counts, load_zone_boundaries,
)

class AllocationTests(unittest.TestCase):
    def test_largest_remainder_preserves_total(self):
        result = largest_remainder_counts({"1": 0.51, "2": 0.30, "3": 0.19}, 11)
        self.assertEqual(result, {"1": 6, "2": 3, "3": 2})

    def test_bounded_allocation_redistributes_shortage(self):
        result, unmet = bounded_allocation({"1": 0.5, "2": 0.3, "3": 0.2}, {"1": 2, "2": 10, "3": 10}, 10)
        self.assertEqual(sum(result.values()), 10)
        self.assertEqual(result["1"], 2)
        self.assertEqual(unmet, 0)

    def test_bounded_allocation_reports_unmet(self):
        result, unmet = bounded_allocation({"1": 1.0}, {"1": 2}, 5)
        self.assertEqual(result, {"1": 2})
        self.assertEqual(unmet, 3)

class ZoneTests(unittest.TestCase):
    def test_classifier_returns_middle_and_basic_zone(self):
        classifier = ZoneClassifier([ZoneBoundary("501", "5", Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]))])
        self.assertEqual(classifier.classify(1, 1), ("5", "501"))
        self.assertIsNone(classifier.classify(3, 3))

    def test_official_workbook_places_target_in_zone_five(self):
        path = Path(__file__).resolve().parents[2] / "中京PTデータ/中ゾーンコード表/pt_system_code_table.xlsx"
        if not path.exists():
            self.skipTest("local official zone workbook is unavailable")
        boundaries, audit = load_zone_boundaries(path)
        classifier = ZoneClassifier(boundaries)
        self.assertEqual(classifier.classify(136.88397984, 35.16967402), ("5", "510"))
        self.assertGreater(audit["loaded_boundaries"], 200)
        self.assertGreater(audit["truncated_or_invalid_wkt_rows"], 0)

class ReproducibilityTests(unittest.TestCase):
    def test_priority_is_reproducible(self):
        self.assertEqual(deterministic_priority(42, 10), deterministic_priority(42, 10))
        self.assertNotEqual(deterministic_priority(42, 10), deterministic_priority(43, 10))

if __name__ == "__main__":
    unittest.main()
