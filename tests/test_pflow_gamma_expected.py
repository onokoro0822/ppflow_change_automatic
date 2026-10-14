import unittest
from types import SimpleNamespace
import numpy as np
from pflow_gamma_expected import ExpectedModel,SHARE
class ExpectedProbabilityTest(unittest.TestCase):
    def model(self):
        m=ExpectedModel.__new__(ExpectedModel);m.target=SimpleNamespace(gcode='23105');m.factor=1;m.c=dict(beta=0,gamma=0)
        m.mesh_cap=10;m.base_sum=np.array([10.]);m.member=np.array([1.]);m.facility_competing=np.array([100.]);m.competing=100.
        m.names=['city'];m.units_target='city';m.nu=1;m.ti=0;m.delta=np.array([[1.]]);m.numerator=np.array([[10.]]);m.indicator=np.array([0.])
        m.groups=[dict(origin='23105',origin_unit='city',n=2,ids=np.array([0]),oi=0,distance=np.array([0.]),fixed=np.array([0.]),den=np.array([10.,20.]),num=np.array([[10.],[20.]]),w=np.array([1.,2.]))]
        return m
    def test_capacity_share_and_destination_conservation(self):
        m=self.model();r=m.predict([0,1,0,100])
        self.assertAlmostEqual(r['arrivals'],2*SHARE*.5)
        self.assertAlmostEqual(sum(r['destinations'].values()),2)
        self.assertAlmostEqual(r['expected_selection_failures'],0)
        self.assertAlmostEqual(m.predict([0,1,50,100])['arrivals'],r['arrivals'])
    def test_zero_facility_and_invalid_parameters(self):
        m=self.model();self.assertEqual(m.predict([0,1,0,0])['arrivals'],0)
        with self.assertRaises(ValueError):m.predict([0,0,0,1])
    def test_shared_mesh_fallback_changes_physical_city_and_count(self):
        m=self.model();m.names=['other','city'];m.nu=2;m.delta=np.array([[1.,0.]]);m.numerator=np.array([[10.,0.]])
        m.groups[0]['num']=np.array([[10.,0.],[20.,0.]])
        r=m.predict([0,1,0,100]);self.assertAlmostEqual(r['destinations']['city'],r['arrivals'])
        self.assertAlmostEqual(sum(r['destinations'].values()),2)
