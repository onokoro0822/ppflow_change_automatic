"""One-weight A calibration. Generates activities; never edits/reselects CSV rows."""
import argparse,json,math,os,subprocess
from pathlib import Path
from pflow_gamma_optimize import ROOT,save,options,check_generation_logs
from pflow_scenario_metrics import references,measure
from pflow_scenario_compare import compare
from pflow_target import AnalysisTarget

MESH_ADD=745.942954248366
GAMMA_ROWS=306.
MAX_WEIGHT=1e8

def next_weight(history,goal):
    low=[x for x in history if x['arrivals']<goal];high=[x for x in history if x['arrivals']>goal]
    pairs=[(a,b) for a in low for b in high if a['weight']<b['weight']]
    if pairs:
        a,b=min(pairs,key=lambda pair:math.log(pair[1]['weight']/pair[0]['weight']))
        fraction=(goal-a['arrivals'])/(b['arrivals']-a['arrivals'])
        weight=math.exp(math.log(a['weight'])+fraction*math.log(b['weight']/a['weight']))
    else:
        last=history[-1];ratio=goal/max(1,last['arrivals']);weight=last['weight']*max(.25,min(4.,ratio))
    return max(.001,min(MAX_WEIGHT,weight))

def settings(weight,closed=False):
    scenario=json.loads((ROOT/'config/gamma/meitetsu_closed.json').read_text())
    gamma=json.loads((ROOT/'config/gamma/postclosure_b.json').read_text())
    if not closed:
        target=AnalysisTarget.load(ROOT/'config/gamma/analysis_target.json')
        scenario.update(meshCapacityAdd=MESH_ADD,injectFacility=True,independentFacilityWeight=weight,
                        facility=dict(id=target.facilityId,longitude=target.longitude,latitude=target.latitude,capacity=1.))
        gamma['indicators'][target.gcode]+=GAMMA_ROWS
    return scenario,gamma

class Runner:
    def __init__(self,output,full_home):
        self.out=output.resolve();self.full_home=full_home
        self.c=json.loads((ROOT/'config/gamma/adopted_b.json').read_text())['coefficients']
        self.target=AnalysisTarget.load(ROOT/'config/gamma/analysis_target.json')
        self.units,self.oref,self.dref=references(ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_d5_shopping_facility_age.csv',ROOT.parent/'中京PTデータ/chukyo_pt_2022_od_purpose_mode_raw.csv')
    def run(self,label,weight,seed=42,factor=50,closed=False):
        case=self.out/label;case.mkdir(parents=True,exist_ok=True)
        identity=dict(weight=weight,seed=seed,sample_factor=factor,closed=closed)
        if (case/'metrics.json').exists():
            data=json.loads((case/'metrics.json').read_text())
            if data['input']!=identity:raise ValueError('Resume inputs differ: '+label)
            return data
        scenario,gamma=settings(weight,closed);scenario['scenarioId']=label
        save(case/'scenario.json',scenario);save(case/'gamma.json',gamma)
        # Small samples remain local; full activities are written only to the SSD.
        destination=(self.full_home/'output/independent_A_20261015'/label) if factor==1 else case/'generated'
        extra=options(self.c,case/'gamma.json',self.c['distance_multiplier']).replace('--large-store-facility-only on','--large-store-facility-only off')
        command=['bash',str(ROOT/'scripts/run_pflow_capacity_sweep.sh'),'--scenario-file',str(case/'scenario.json'),'--output-root',str(destination),'--seed',str(seed),'--sample-factor',str(factor),'--pflow-home',str(self.full_home if factor==1 else ROOT/'.local/pflow'),'--skip-compile','--pflow-args',extra]
        metadata=dict(command=command,input=identity,fixed=dict(mesh_add=MESH_ADD,gamma_add_rows=GAMMA_ROWS,coefficients=self.c),analysis_target=self.target.as_dict())
        if (case/'command.json').exists() and json.loads((case/'command.json').read_text())!=metadata:raise ValueError('Command changed: '+label)
        save(case/'command.json',metadata);directory=destination/'scenario'
        if not (directory/'.complete').exists():
            if (directory/'23').exists():raise ValueError('Incomplete output retained: '+str(directory))
            if factor==1 and not (self.full_home/'data/facilities').exists():raise FileNotFoundError('Connect full-data SSD: '+str(self.full_home))
            env=dict(os.environ);env['PATH']='/Users/yuuki/tools/apache-maven-3.6.3/bin:'+env['PATH'];env['MAVEN_OPTS']='-Xmx6g'
            print('GENERATE',label,weight,flush=True)
            with (case/'run.log').open('w') as log:subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        check_generation_logs(directory);data=measure(directory,self.target,self.units,self.oref,self.dref)
        data.update(input=identity,arrivals_full_equivalent=data['counts'].get('facility',0)*factor)
        save(case/'metrics.json',data);print('RESULT',label,data['counts'],data['origin_tvd'],flush=True);return data

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--full-home',type=Path,default=Path('/Volumes/PFLOW_SSD/PFLOW'));p.add_argument('--pilot-only',action='store_true');p.add_argument('--resume',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=a.resume);runner=Runner(a.output,a.full_home)
    path=a.output/'report.json';report=json.loads(path.read_text()) if a.resume and path.exists() else dict(status='running',method='A independent alternative',targets={},controls={},notes=['Only dedicated facility weight is calibrated.','PT large-store origins remain a reference although ordinary shopping can visit too.','Existing destination ratios conditional on not choosing the new alternative stay fixed at each origin/city.','No CSV rewrite, quotas or visitor reselection.'])
    for goal in [3000,6000]:
        key=str(goal);record=report['targets'].setdefault(key,dict(pilots=[],full=[]));history=[];weight=4000.*goal/3000
        for iteration in range(3):
            row=runner.run(f't{goal}_pilot{iteration}_s42',weight)
            history.append(dict(weight=weight,arrivals=row['arrivals_full_equivalent']))
            if len(record['pilots'])<=iteration:record['pilots'].append(row)
            save(path,report)
            if abs(row['arrivals_full_equivalent']/goal-1)<=.10:break
            weight=next_weight(history,goal)
        # Calibrate to the four-seed mean before full generation.
        seeds=[runner.run(f't{goal}_mean_s{s}',weight,seed=s) for s in range(42,46)]
        mean=sum(r['arrivals_full_equivalent'] for r in seeds)/4
        record['pilot_seeds']=seeds;record['pilot_mean']=mean
        weight=next_weight([dict(weight=weight,arrivals=mean)],goal) if abs(mean/goal-1)>.02 else weight
        record['initial_full_weight']=weight;save(path,report)
        if a.pilot_only:continue
        full_history=[]
        for iteration in range(4):
            row=runner.run(f't{goal}_full{iteration}',weight,factor=1)
            if len(record['full'])<=iteration:record['full'].append(row)
            full_history.append(dict(weight=weight,arrivals=row['arrivals_full_equivalent']));save(path,report)
            if abs(row['arrivals_full_equivalent']/goal-1)<=.02:break
            weight=next_weight(full_history,goal)
        best=min(record['full'],key=lambda r:abs(r['arrivals_full_equivalent']-goal));record['best']=best
        record['count_met']=abs(best['arrivals_full_equivalent']/goal-1)<=.02;save(path,report)
        weight=best['input']['weight']
        record['final_seeds']=[runner.run(f't{goal}_final_s{s}',weight,seed=s) for s in range(42,46)];save(path,report)
    if not a.pilot_only:
        for name,closed in [('closed',True),('fixed_inputs_zero_weight',False)]:
            report['controls'][name]=runner.run(name,0.,factor=1,closed=closed);save(path,report)
            for goal,record in report['targets'].items():
                audit=a.output/f't{goal}_comparison_{name}.json'
                if audit.exists():comparison=json.loads(audit.read_text())
                else:
                    print('COMPARE',goal,name,flush=True)
                    comparison=compare(Path(report['controls'][name]['directory']),Path(record['best']['directory']),runner.target);save(audit,comparison)
                record.setdefault('comparisons',{})[name]=comparison;save(path,report)
    report['status']='pilot_finished' if a.pilot_only else 'finished';save(path,report)
if __name__=='__main__':main()
