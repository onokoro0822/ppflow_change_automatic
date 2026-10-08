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


class ScheduleTest(unittest.TestCase):
    def make(self, previous_lon, previous_duration, duration, next_lon=None):
        return Candidate(
            file_index=0, row_index=1, person_id="p", unit="u", at_facility=False,
            previous_lon=previous_lon, previous_lat=combined.TARGET_LAT, lon=0.0, lat=0.0,
            previous_duration=previous_duration, duration=duration,
            next_lon=next_lon, next_lat=combined.TARGET_LAT if next_lon is not None else None,
        )

    def test_inbound_trip_must_fit_previous_activity(self):
        far = combined.TARGET_LON + 0.11  # about 10 km east: 30 minutes at 20 km/h
        self.assertFalse(combined.fits_schedule_at_facility(self.make(far, 20 * 60, 3600), 20))
        self.assertTrue(combined.fits_schedule_at_facility(self.make(far, 40 * 60, 3600), 20))

    def test_outbound_trip_must_fit_shopping_stay(self):
        far = combined.TARGET_LON + 0.11
        near = combined.TARGET_LON
        self.assertFalse(combined.fits_schedule_at_facility(self.make(near, 3600, 20 * 60, far), 20))
        self.assertTrue(combined.fits_schedule_at_facility(self.make(near, 3600, 40 * 60, far), 20))

    def test_select_excludes_arrivals_that_do_not_fit(self):
        far = combined.TARGET_LON + 0.11
        candidates = [self.make(far, 60, 3600), self.make(combined.TARGET_LON, 3600, 3600)]
        candidates[1] = Candidate(**{**candidates[1].__dict__, "person_id": "q", "row_index": 2})
        selected, audit = combined.select(candidates, {"u": 1.0}, 1, seed=1, feasibility_speed_kmh=20)
        self.assertEqual([c.person_id for c in selected], ["q"])
        self.assertEqual(audit["excluded_by_schedule"], {"other": 1})


class StartFromBaselineTest(unittest.TestCase):
    def test_capacity_side_effects_are_dropped(self):
        baseline = [ActivityFile(Path("p.csv"), [
            ["1", "30", "1", "1", "0", "100", "1", "136.900000", "35.100000", "23110"],
            ["1", "30", "1", "1", "100", "100", "100", "136.950000", "35.150000", "23106"],
            ["2", "40", "2", "1", "0", "100", "1", "136.800000", "35.200000", "23203"],
            ["2", "40", "2", "1", "100", "100", "100", "136.700000", "35.300000", "23203"],
        ])]
        calibrated = [ActivityFile(Path("p.csv"), [
            baseline[0].rows[0], baseline[0].rows[1], baseline[0].rows[2],
            ["2", "40", "2", "1", "100", "100", "100", "136.884000", "35.170000", "23105"],
        ])]
        added = candidate(1, "1", "中川区", False)
        output, _ = combined.apply_selection(
            calibrated, baseline, [added], [added], start_from_baseline=True
        )
        rows = output[0].rows
        self.assertEqual(rows[1][7:], [*FAC, combined.TARGET_GCODE])
        self.assertEqual(rows[3], baseline[0].rows[3])


class MatchAttributesTest(unittest.TestCase):
    def make(self, index, person, unit, age, gender, hour, at_facility=False):
        return Candidate(
            file_index=0, row_index=index, person_id=person, unit=unit, at_facility=at_facility,
            previous_lon=136.9, previous_lat=35.1, lon=136.95, lat=35.15,
            age=age, gender=gender, start=hour * 3600,
        )

    def test_matches_all_marginals_when_candidates_allow(self):
        candidates = [
            self.make(1, "a", "中村区", 35, "1", 10),
            self.make(2, "b", "中村区", 72, "2", 14),
            self.make(3, "c", "一宮市", 35, "1", 14),
            self.make(4, "d", "一宮市", 72, "2", 10),
            self.make(5, "e", "中村区", 35, "1", 10, at_facility=True),
        ]
        targets = {
            "origin": {"中村区": 0.5, "一宮市": 0.5},
            "age_gender": {"70～79歳|female": 1.0},
            "hour": {"10": 0.5, "14": 0.5},
        }
        selected, audit = combined.select_matching_attributes(candidates, targets, 2, seed=1)
        self.assertEqual({c.person_id for c in selected}, {"b", "d"})
        self.assertEqual(audit["shortfall"], 0)
        self.assertEqual(audit["allocated"]["hour"], {"14": 1, "10": 1})

    def test_prefers_capacity_arrivals_within_a_cell(self):
        candidates = [
            self.make(1, "a", "中村区", 35, "1", 10),
            self.make(2, "b", "中村区", 35, "1", 10, at_facility=True),
        ]
        targets = {"origin": {"中村区": 1.0}, "age_gender": {"30～39歳|male": 1.0}, "hour": {"10": 1.0}}
        selected, _ = combined.select_matching_attributes(candidates, targets, 1, seed=1)
        self.assertEqual(selected[0].person_id, "b")


if __name__ == "__main__":
    unittest.main()
