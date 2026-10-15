import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import pflow_gamma_controls as controls

class ControlsTest(unittest.TestCase):
    def test_sample_controls_resume_without_ssd_or_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'report.json';out=root/'controls'
            source.write_text(json.dumps({'targets':{'3000':{'best':{'parameters':[0,.7,1,1],'directory':'unused-full-output'}}}}))
            calls=[]
            def generate(cmd,**kwargs):
                calls.append(cmd)
                dest=Path(cmd[cmd.index('--output-root')+1])/'scenario'
                (dest/'logs').mkdir(parents=True);(dest/'.complete').touch()
            def measure(directory,*args):return {'directory':str(directory),'counts':{}}
            args=['controls','--report',str(source),'--output',str(out),'--sample-only','--full-home',str(root/'absent-ssd')]
            with patch.object(controls,'references',return_value=(None,None,None)),patch.object(controls,'measure',side_effect=measure),patch.object(controls,'compare',return_value={'counts':{}}),patch.object(controls.subprocess,'run',side_effect=generate):
                with patch('sys.argv',args):controls.main()
                self.assertEqual(len(calls),2)
                with patch('sys.argv',args+['--resume']):controls.main()
                self.assertEqual(len(calls),2)
                result=json.loads((out/'controls.json').read_text())['3000']
                self.assertIn('closure_paired_comparison',result)
                self.assertNotIn('postclosure_f1',result)
