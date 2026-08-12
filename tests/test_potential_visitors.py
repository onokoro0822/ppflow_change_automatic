from __future__ import annotations

import unittest

from potential_visitors import geometric_preference_score


class PotentialVisitorsTest(unittest.TestCase):
    def test_geometric_preference_score(self) -> None:
        self.assertAlmostEqual(geometric_preference_score([1.0, 4.0]), 2.0)
        self.assertAlmostEqual(geometric_preference_score([1.0, 1.0, 1.0]), 1.0)

    def test_geometric_preference_score_rejects_nonpositive_ratio(self) -> None:
        with self.assertRaises(ValueError):
            geometric_preference_score([1.0, 0.0])


if __name__ == "__main__":
    unittest.main()
