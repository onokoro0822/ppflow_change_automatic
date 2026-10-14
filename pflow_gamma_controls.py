"""Generate coefficient-matched controls after A optimization has finished.

Keeping the optimized distance coefficient in all three states isolates the
input changes representing closure/new supply from a global coefficient change.
"""
import argparse,json,os,subprocess
from pathlib import Path
from pflow_gamma_optimize import ROOT,save,options
from pflow_scenario_metrics import references,measure
from pflow_scenario_compare import compare
from pflow_target import AnalysisTarget

def main():
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--full-home',type=Path,default=Path('/Volumes/PFLOW_SSD/PFLOW'));a=p.parse_args()
    report=json.loads(a.report.read_text());out=a.output.resolve();out.mkdir(exist_ok=False,parents=True)
    target=AnalysisTarget.load(ROOT/'config/gamma/analysis_target.json');c=json.loads((ROOT/'config/gamma/adopted_b.json').read_text())['coefficients']
    units,oref,dref=references(ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_d5_shopping_facility_age.csv',ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_purpose_mode_raw.csv')
    env=dict(os.environ);env['PATH']='/Users/yuuki/tools/apache-maven-3.6.3/bin:'+env['PATH'];env['MAVEN_OPTS']='-Xmx6g';results={}
    for goal,record in report['targets'].items():
        best=record['best'];distance=best['parameters'][1];results[goal]={}
        for state,factor in [('preclosure',50),('postclosure',50),('postclosure',1)]:
            label=f't{goal}_{state}_f{factor}';case=out/label;case.mkdir()
            scenario=json.loads((ROOT/'config/gamma/meitetsu_closed.json').read_text())
            if state=='preclosure':scenario.pop('retailExclusion');scenario['meshCapacityRemove']=0.
            save(case/'scenario.json',scenario)
            destination=a.full_home/'output/gamma_A_controls_52365790_20261014'/label if factor==1 else case/'generated'
            cmd=['bash',str(ROOT/'scripts/run_pflow_capacity_sweep.sh'),'--scenario-file',str(case/'scenario.json'),'--output-root',str(destination),'--seed','42','--sample-factor',str(factor),'--pflow-home',str(a.full_home if factor==1 else ROOT/'.local/pflow'),'--skip-compile','--pflow-args',options(c,ROOT/'config/gamma'/f'{state}_b.json',distance)]
            save(case/'command.json',dict(command=cmd,distance=distance,analysis_target=target.as_dict()));print('CONTROL',label,flush=True)
            with (case/'run.log').open('w') as log:subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
            directory=destination/'scenario'
            for log in (directory/'logs').glob('*.log'):
                text=log.read_text()
                if '[ERROR]' in text or 'Exception' in text or 'Unresolved compilation' in text:raise RuntimeError(str(log))
            metrics=measure(directory,target,units,oref,dref);results[goal][f'{state}_f{factor}']=metrics
            save(case/'metrics.json',metrics);save(out/'controls.json',results)
            if factor==1:
                paired=compare(directory,Path(best['directory']),target);save(case/'comparison_to_new.json',paired)
                results[goal]['full_paired_comparison']=paired;save(out/'controls.json',results)
        pre=results[goal]['preclosure_f50'];post=results[goal]['postclosure_f50']
        results[goal]['closure_paired_comparison']=compare(Path(pre['directory']),Path(post['directory']),target);save(out/'controls.json',results)
if __name__=='__main__':main()
