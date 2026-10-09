import unittest
import csv
import math
import tempfile
from pathlib import Path

import numpy as np

import pflow_mnl_estimation as estimation


class HelpersTest(unittest.TestCase):
    def test_agent_type_matches_param_files(self):
        self.assertEqual(estimation.agent_type(21), "worker")
        self.assertEqual(estimation.agent_type(23), "nolabor")
        self.assertEqual(estimation.agent_type(12), "student1")
        self.assertEqual(estimation.agent_type(15), "student2")

    def test_city_type_thresholds_follow_data_accessor(self):
        self.assertEqual(estimation.city_type(99_999), 1)
        self.assertEqual(estimation.city_type(100_000), 2)
        self.assertEqual(estimation.city_type(500_000), 3)


class SharedMeshTest(unittest.TestCase):
    def test_city_membership_matches_java_including_prefecture_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (root / "city_boundary.csv").open("w", newline="") as h:
                w = csv.writer(h)
                w.writerow(["n03_007", "city_pop", "area", "pop_ratio", "office_ratio", "st_x", "st_y"])
                for code in ["23104", "23105", "24201"]:
                    w.writerow([code, 100000, 10, 1, 1, 136, 35])
            with (root / "mesh_ecensus.csv").open("w", newline="") as h:
                w = csv.writer(h)
                w.writerow(["mesh", "gcode", "e0", "e1", "e2", "e3", "shopping"])
                for mesh, code, cap in [("shared", "invalid", 999),
                                         ("shared", "23104", 10),
                                         ("shared", "23105", 90),
                                         ("shared", "23105", 90),
                                         ("local", "23104", 5),
                                         ("cross", "24201", 20),
                                         ("cross", "23105", 99)]:
                    w.writerow([mesh, code, 0, 0, 0, 0, cap])
            (root / "city_retail.csv").write_text("header\n")
            result = estimation.load_cities(root)
            self.assertEqual(set(result), {"23104", "23105"})
            self.assertAlmostEqual(math.expm1(result["23104"]["ln_wholesale_retail_employees"]), 15)
            self.assertAlmostEqual(math.expm1(result["23105"]["ln_wholesale_retail_employees"]), 30)


class LikelihoodTest(unittest.TestCase):
    class FakeModel:
        def __init__(self, prob):
            self.prob = prob

        def unit_probabilities(self, beta, distance_scale):
            return self.prob

    def test_unreachable_pairs_are_reported_and_skipped(self):
        prob = np.array([[0.5, 0.5], [0.0, 1.0]])
        od = np.array([[2.0, 2.0], [1.0, 3.0]])
        ll, unreachable = estimation.log_likelihood(self.FakeModel(prob), od, 0.0, 1.0)
        self.assertAlmostEqual(ll, 4 * np.log(0.5))
        self.assertAlmostEqual(unreachable, 1 / 8)


if __name__ == "__main__":
    unittest.main()
