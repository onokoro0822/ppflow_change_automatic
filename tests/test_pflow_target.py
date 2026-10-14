import csv,json,tempfile,unittest
from pathlib import Path
from pflow_target import AnalysisTarget
from pflow_capacity_analysis import summarize_scenario
class TargetTest(unittest.TestCase):
    def test_same_coordinate_existing_store_not_counted_as_new(self):
        t=AnalysisTarget(meshCode='52365790',longitude=136.88665075,latitude=35.16649051,facilityId=900000001)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'x.csv'
            rows=[[1,30,1,21,0,10,1,136,35,'23105'],[1,30,1,21,10,10,100,t.longitude,t.latitude,'23105'],[1,30,1,21,20,10,100,t.longitude,t.latitude,'23105']]
            with p.open('w') as f:csv.writer(f).writerows(rows)
            trace=Path(str(p)+'.branches.tsv');trace.write_text('person_id\tactivity_index\tstart\tpurpose\tlarge_store\tfacility_id\n1\t0\t0\t1\t-\t-\n1\t1\t10\t100\t0\t0\n1\t2\t20\t100\t1\t900000001\n')
            result=summarize_scenario(Path(tmp),t)
            self.assertEqual(result['target_facility'],1);self.assertEqual(result['target_mesh'],2)
            trace.unlink()
            with self.assertRaises(FileNotFoundError):summarize_scenario(Path(tmp),t)
    def test_config_must_match_mesh(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'target.json';p.write_text(json.dumps({'meshCode':'52365790'}))
            with self.assertRaises(ValueError):AnalysisTarget.load(p)
