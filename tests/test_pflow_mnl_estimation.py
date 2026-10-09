import unittest

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
