import unittest
import numpy as np
from pflow_accessibility_estimation import accessibility, mesh_center, row_split, fit


class AccessibilityTest(unittest.TestCase):
    def test_no_distance_linear_case_is_city_sum(self):
        cap=np.array([10.,20.,30.]);dist=np.array([[0.,5.,10.],[1.,2.,3.]])
        member=np.array([[1.,0.],[1.,1.],[0.,1.]])
        actual=accessibility(cap,dist,member,1,0)
        np.testing.assert_allclose(actual,np.tile(np.log1p([30.,50.]),(2,1)))

    def test_shared_mesh_add_reaches_both_cities_and_distance_changes_effect(self):
        cap=np.array([10.,20.]);dist=np.array([[0.,10.],[10.,1.]])
        member=np.array([[1.,1.],[0.,1.]])
        before=accessibility(cap,dist,member,1,2)
        after=accessibility(cap+np.array([5.,0.]),dist,member,1,2)
        self.assertTrue(np.all(after>before))
        self.assertGreater((after-before)[0,1],(after-before)[1,1])

    def test_origin_split_disjoint_and_repeatable(self):
        first=row_split(60);second=row_split(60)
        for a,b in zip(first,second):np.testing.assert_array_equal(a,b)
        self.assertEqual(len(set(np.concatenate(first))),60)
        self.assertEqual([len(x) for x in first],[36,12,12])

    def test_fit_starts_from_neutral_not_full_data_calibration(self):
        class FlatModel:
            def probabilities(self, coefficients, feature):
                return np.ones((1,1))
        coefficients,_=fit(FlatModel(),np.zeros((1,1)),np.array([[10.]]),np.array([0]),np.ones((1,1),dtype=bool))
        np.testing.assert_array_equal(coefficients,[0.,1.,1.,1.,1.])

    def test_mesh_center_and_invalid_parameters(self):
        lon,lat=mesh_center('52366700')
        self.assertAlmostEqual(lon,136.88125)
        self.assertAlmostEqual(lat,35.170833333333334)
        with self.assertRaises(ValueError):accessibility(np.array([-1]),np.ones((1,1)),np.ones((1,1)),1,0)
