import json,unittest
import numpy as np
from pflow_gamma_optimize import optimize
class Model:
    c=dict(distance_multiplier=.917)
    facility_competing=np.array([100.])
    def predict(self,x):
        return dict(arrivals=10000*x[3]/(100+x[3]),origins={'a':1},destinations={'a':1})
class OptimizerTest(unittest.TestCase):
    def test_serializable_count_profile_result(self):
        # Bounds deliberately make this goal attainable with capacity >= 1000.
        result=optimize(Model(),9500,{'a':1},{'a':1})
        self.assertAlmostEqual(result['predicted']['arrivals'],9500,places=5)
        self.assertGreater(len(json.dumps(result)),0)
