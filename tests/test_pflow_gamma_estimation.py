import csv,tempfile,unittest
from collections import Counter
from pathlib import Path
import numpy as np
from pflow_gamma_estimation import load_indicators,nelder_mead,numeric_hessian,conditional_score,GammaModel
from pflow_mnl_estimation import DestinationModel,AGENT_PARAM_FILES

class GammaEstimationTest(unittest.TestCase):
 def test_area_and_presence_have_different_addition_effects(self):
  from pflow_gamma_estimation import area_feature
  self.assertEqual(float(area_feature(100000+54374,'presence')-area_feature(100000,'presence')),0.)
  self.assertGreater(float(area_feature(100000+54374,'log1p_area')-area_feature(100000,'log1p_area')),0.)

 def test_optimizer_and_numeric_hessian_recover_quadratic(self):
  a=np.array([[4.,1.],[1.,2.]]);target=np.array([1.3,-.7])
  fun=lambda x:float((x-target)@a@(x-target)/2)
  x,info=nelder_mead(fun,[0.,0.]);self.assertTrue(info['converged']);np.testing.assert_allclose(x,target,atol=1e-6)
  np.testing.assert_allclose(numeric_hessian(fun,x),a,atol=1e-6)
 def test_indicators_require_sources_coverage_and_variation(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'indicator.csv'
   p.write_text('gcode,indicator,source\n23101,0,inventory\n23105,1,inventory\n')
   np.testing.assert_array_equal(load_indicators(p,['23101','23105'])[0],[0,1])
   for body in ['23101,0,x\n','23101,0,x\n23105,0,x\n','23101,0,x\n23105,1,\n','23101,0,x\n23101,1,x\n','23101,0,x\n23105,nan,x\n']:
    p.write_text('gcode,indicator,source\n'+body)
    with self.assertRaises(ValueError):load_indicators(p,['23101','23105'])
 def test_holdout_observations_do_not_enter_training_score(self):
  p=np.array([[.5,.25,.25],[.2,.7,.1],[.1,.1,.8]])
  od=np.array([[4.,2.,9.],[2.,7.,8.],[50.,60.,70.]])
  train=np.array([True,True,False]);a=conditional_score(p,od,train,train)
  od[2]*=100;od[:,2]*=100
  self.assertEqual(a,conditional_score(p,od,train,train))
  self.assertAlmostEqual(a['trips'],15.)
 def test_gamma_zero_matches_lab_and_nonzero_changes_destination_probability(self):
  class Units:
   def unit_for_gcode(self,c):return c
  cities={str(i):dict(type=1,lon=136+i*.01,lat=35,area=1+i,pop_ratio=i*2,office_ratio=i,ln_retail=i+.1) for i in range(3)}
  params={k:{1:[-5,0,.3,0,.01,.1,.1]} for k in AGENT_PARAM_FILES}
  weights={str(i):Counter(worker=10) for i in range(3)}
  original=DestinationModel(cities,params,weights,Units());model=GammaModel(original,cities,params,[0,1,0])
  np.testing.assert_allclose(model.probabilities([0,1,1,1,1],False),original.unit_probabilities(0,1))
  base=model.probabilities([0,1,1,1,1],False);changed=model.probabilities([1,1,1,1,1],False)
  self.assertTrue(np.all(changed[:,1]>base[:,1]));np.testing.assert_allclose(changed.sum(axis=1),1)
  np.testing.assert_allclose(model.probabilities([0,.2,1,1,1,1],True),original.unit_probabilities(.2,1))
if __name__=='__main__':unittest.main()

class GammaPairSyntheticTest(unittest.TestCase):
 def test_pair_recovers_known_coefficients_and_never_adopts(self):
  from pflow_gamma_estimation import fit_pair
  class Synthetic:
   basis=np.random.default_rng(23).normal(size=(6,9,9))
   def probabilities(self,x,include_beta):
    co=np.asarray(x) if include_beta else np.insert(x,1,0.)
    v=np.einsum('i,ijk->jk',co,self.basis);v-=v.max(axis=1,keepdims=True);e=np.exp(v);return e/e.sum(axis=1,keepdims=True)
  m=Synthetic();truth=[.4,.3,.8,.2,-.1,.25];od=10000*m.probabilities(truth,True)
  r=fit_pair(m,od,[False]*7+[True]*2)
  self.assertIsNone(r['adopted_model']);self.assertEqual(r['recommendation'],'b')
  np.testing.assert_allclose(list(r['models']['b']['coefficients'].values()),truth,atol=1e-4)
  self.assertTrue(r['models']['b']['identified'])
