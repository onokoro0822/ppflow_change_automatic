import unittest
from pflow_independent_calibration import next_weight,settings,MESH_ADD
class IndependentCalibrationTest(unittest.TestCase):
    def test_fixed_inputs_and_zero_control(self):
        a,g=settings(0);b,h=settings(50000)
        self.assertEqual(g,h);a.pop('independentFacilityWeight');b.pop('independentFacilityWeight');self.assertEqual(a,b)
        self.assertEqual(a['meshCapacityAdd'],MESH_ADD);self.assertEqual(g['indicators']['23105'],325)
        closed,c=settings(0,True);self.assertFalse(closed['injectFacility']);self.assertEqual(closed['meshCapacityAdd'],0);self.assertEqual(c['indicators']['23105'],19)
    def test_weight_update_stays_in_bracket(self):
        result=next_weight([dict(weight=100,arrivals=1000),dict(weight=10000,arrivals=7000)],3000)
        self.assertGreater(result,100);self.assertLess(result,10000)
    def test_zero_arrivals_increases_weight_without_division_by_zero(self):
        self.assertEqual(next_weight([dict(weight=100,arrivals=0)],3000),400)
