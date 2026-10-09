import math
import unittest
from pflow_retail_floor_area import employee_increment


class EmployeeIncrementTest(unittest.TestCase):
    def test_source_area_is_thousand_square_metres(self):
        self.assertEqual(employee_increment(2000, 100, 2), 100)
        self.assertAlmostEqual(employee_increment(54374, 65068, 4743), 745.942954248366)

    def test_zero_use_and_proportional_floor_allocation(self):
        self.assertEqual(employee_increment(0, 100, 2), 0)
        self.assertAlmostEqual(employee_increment(600, 100, 2) +
                               employee_increment(400, 100, 2),
                               employee_increment(1000, 100, 2))

    def test_invalid_measurements_fail(self):
        for values in [(-1, 1, 1), (math.nan, 1, 1), (math.inf, 1, 1),
                       (1, 0, 1), (1, math.nan, 1), (1, 1, 0), (1, 1, math.inf)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                employee_increment(*values)
