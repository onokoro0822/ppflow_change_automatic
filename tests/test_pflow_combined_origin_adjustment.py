import unittest
from pathlib import Path

import pflow_combined_origin_adjustment as combined
from pflow_combined_origin_adjustment import ActivityFile, Candidate

FAC = (combined.FACILITY_LON_TEXT, combined.FACILITY_LAT_TEXT)


def candidate(index, person, unit, at_facility):
    return Candidate(
        file_index=0, row_index=index, person_id=person, unit=unit, at_facility=at_facility,
        previous_lon=136.9, previous_lat=35.1, lon=136.95, lat=35.15,
    )


class SelectTest(unittest.TestCase):
    def test_keeps_facility_arrivals_first_and_follows_weights(self):
        candidates = [
            candidate(1, "a", "中村区", True),
            candidate(2, "b", "中村区", True),
            candidate(3, "c", "中村区", True),
            candidate(4, "d", "一宮市", False),
            candidate(5, "e", "一宮市", False),
        ]
        selected, audit = combined.select(candidates, {"中村区": 0.5, "一宮市": 0.5}, 4, seed=1)
        self.assertEqual(audit["allocated"], {"中村区": 2, "一宮市": 2})
        self.assertEqual(audit["shortfall"], 0)
        kept = [c for c in selected if c.unit == "中村区"]
        self.assertTrue(all(c.at_facility for c in kept))
        self.assertEqual({c.person_id for c in selected if c.unit == "一宮市"}, {"d", "e"})

    def test_one_arrival_per_person_and_outside_units_ignored(self):
        candidates = [
            candidate(1, "a", "中区", False),
            candidate(2, "a", "中区", False),
            candidate(3, "b", combined.OUTSIDE_AICHI, False),
        ]
        selected, audit = combined.select(candidates, {"中区": 1.0}, 2, seed=1)
        self.assertEqual(len(selected), 1)
        self.assertEqual(audit["shortfall"], 1)


class ApplySelectionTest(unittest.TestCase):
    def test_moves_added_and_restores_released_from_baseline(self):
        calibrated = [ActivityFile(Path("p.csv"), [
            ["1", "30", "1", "1", "0", "100", "1", "136.900000", "35.100000", "23110"],
            ["1", "30", "1", "1", "100", "100", "100", "136.950000", "35.150000", "23106"],
            ["2", "40", "2", "1", "0", "100", "1", "136.800000", "35.200000", "23203"],
            ["2", "40", "2", "1", "100", "100", "100", *FAC, "23104"],
        ])]
        baseline = [ActivityFile(Path("p.csv"), [
            calibrated[0].rows[0], calibrated[0].rows[1], calibrated[0].rows[2],
            ["2", "40", "2", "1", "100", "100", "100", "136.880000", "35.170000", "23105"],
        ])]
        added = candidate(1, "1", "中川区", False)
        released = candidate(3, "2", "一宮市", True)
        output, counts = combined.apply_selection(calibrated, baseline, [added, released], [added])
        rows = output[0].rows
        self.assertEqual(rows[1][7:], [*FAC, combined.TARGET_GCODE])
        self.assertEqual(rows[3][7:], ["136.880000", "35.170000", "23105"])
        self.assertEqual(counts["added"], 1)
        self.assertEqual(counts["released"], 1)
        self.assertEqual(counts["facility_arrivals_after"], 1)
        self.assertEqual(calibrated[0].rows[1][7], "136.950000")  # input is not mutated

    def test_check_aligned_rejects_changed_activity_sequence(self):
        a = [ActivityFile(Path("p.csv"), [["1", "30", "1", "1", "0", "100", "1", "0", "0", "23105"]])]
        b = [ActivityFile(Path("p.csv"), [["1", "30", "1", "1", "0", "100", "100", "0", "0", "23105"]])]
        with self.assertRaises(ValueError):
            combined.check_aligned(a, b)


if __name__ == "__main__":
    unittest.main()
