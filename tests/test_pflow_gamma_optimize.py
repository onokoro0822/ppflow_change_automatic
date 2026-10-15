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

class ResumeTest(unittest.TestCase):
    def test_recovery_rejects_error_log_before_measurement(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from pflow_gamma_optimize import run_case
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);case=out/'case';case.mkdir();generated=out/'generated'/'scenario'
            (generated/'logs').mkdir(parents=True);(generated/'.complete').touch()
            (generated/'logs'/'Commuter.log').write_text('Unresolved compilation problems')
            x=[1,1,1,1]
            (case/'command.json').write_text(json.dumps(dict(parameters=x,seed=42,sample_factor=50,command=['--output-root',str(generated.parent)])))
            with patch('pflow_gamma_optimize.measure') as measure:
                with self.assertRaises(RuntimeError):run_case(out,'case',x,42,50,None,None,None,None,None,None)
                measure.assert_not_called()
    def test_reuse_rejects_different_seed(self):
        import tempfile
        from pathlib import Path
        from pflow_gamma_optimize import run_case
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);case=out/'case';case.mkdir()
            (case/'metrics.json').write_text(json.dumps(dict(parameters=[1,1,1,1],seed=42,sample_factor=50)))
            with self.assertRaises(ValueError):run_case(out,'case',[1,1,1,1],43,50,None,None,None,None,None,None)
