import unittest,tempfile
from pathlib import Path
from pflow_fixed_activity import apportion,traced_rows
class FixedActivityTest(unittest.TestCase):
 def test_apportion_preserves_total_and_zero(self):
  self.assertEqual(apportion(5,{'a':.5,'b':.5}),{'a':3,'b':2})
  self.assertEqual(apportion(0,{'a':1}),{'a':0})
  self.assertEqual(sum(apportion(6000,{'a':.36364,'b':.43636,'c':.2}).values()),6000)
 def test_invalid_shares_rejected(self):
  for s in ({'a':-1},{'a':float('nan')},{'a':0}):
   with self.assertRaises(ValueError):apportion(4,s)
 def test_trace_identity_and_missing_branch_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.csv';p.write_text('1,40,1,21,0,900,100,136,35,23105\n')
   tr=Path(str(p)+'.branches.tsv');header='person_id\tactivity_index\tstart\tpurpose\tlarge_store\n'
   tr.write_text(header+'1\t0\t0\t100\t1\n');self.assertTrue(list(traced_rows(p))[0][2])
   for line in ['1\t0\t0\t100\t-\n','2\t0\t0\t100\t1\n','']:
    tr.write_text(header+line)
    with self.assertRaises(ValueError):list(traced_rows(p))
 def test_independent_audit_rejects_schedule_and_ordinary_arrivals(self):
  import csv
  from pflow_fixed_activity import validate_output
  from pflow_capacity_analysis import TARGET_LON,TARGET_LAT,TARGET_GCODE,TARGET_MESH
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);base=root/'base';out=root/'out';(base/'23').mkdir(parents=True);(out/'23').mkdir(parents=True)
   old='1,40,1,21,0,900,1,136,35,23105\n1,40,1,21,900,900,100,136.1,35,23105\n'
   (base/'23/x.csv').write_text(old)
   (base/'23/x.csv.branches.tsv').write_text('person_id\tactivity_index\tstart\tpurpose\tlarge_store\n1\t0\t0\t1\t-\n1\t1\t900\t100\t0\n')
   (out/'audit.tsv').write_text('file\tperson_id\tactivity_index\treason\n')
   config={'longitude':TARGET_LON,'latitude':TARGET_LAT,'gcode':TARGET_GCODE,'meshCode':TARGET_MESH,'quotas':{},'units':{'23105':'A'},'downstream':True}
   (out/'23/x.csv').write_text(old);self.assertEqual(validate_output(base,out,config)['audit']['activities'],2)
   (out/'23/x.csv').write_text(old.replace('900,900','1800,900'))
   with self.assertRaisesRegex(AssertionError,'schedule'):validate_output(base,out,config)
   (out/'23/x.csv').write_text(old.replace('136.1,35,23105',f'{TARGET_LON},{TARGET_LAT},{TARGET_GCODE}'))
   with self.assertRaisesRegex(AssertionError,'Ordinary'):validate_output(base,out,config)
